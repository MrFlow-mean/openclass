"use client";

import { useCallback, useEffect, useEffectEvent, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api, getApiWebSocketUrl } from "@/lib/api";
import {
  PROVIDER_LABELS,
  googleRealtimeErrorMessage,
  modelButtonLabel,
  realtimeConnectionErrorMessage,
  websocketMessageText,
  type GoogleRealtimeAudioMessage,
} from "@/components/course-studio/model-catalog";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import { useRealtimeLogQueue } from "@/hooks/use-realtime-log-queue";
import { pcmFloatToBase64, playPcmBase64, resampleLinear } from "@/lib/realtime-audio";
import type { AIModelOption, AIModelSelection, Lesson } from "@/types";

type UseRealtimeVoiceOptions = {
  activeLesson: Lesson | null;
  latestAssistantMessageContent: string | null;
  selectedRealtimeModel: AIModelSelection;
  selectedRealtimeOption: AIModelOption | null | undefined;
  selectedRealtimeTransport: string;
  busyAction: string | null;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  onSubmitTranscript: (message: string) => void;
};

function createClientSessionId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

function realtimeReadyStatus(option: AIModelOption | null | undefined): string {
  if (!option) {
    return "Realtime voice is not enabled";
  }
  if (!option.enabled) {
    return `${PROVIDER_LABELS[option.provider]} realtime voice is not configured`;
  }
  return "Click the microphone to connect realtime Chatbot voice";
}

function realtimeUnavailableMessage(option: AIModelOption | null | undefined): string {
  if (!option) {
    return "Realtime voice is not enabled. Turn on OPENCLASS_REALTIME_ENABLED and restart the backend.";
  }
  return `${PROVIDER_LABELS[option.provider]} realtime voice API key is not configured.`;
}

function isAutomaticRealtimeStatus(value: string): boolean {
  return (
    value === "Realtime voice is not enabled" ||
    value === "Click the microphone to connect realtime Chatbot voice" ||
    value.endsWith("realtime voice is not configured")
  );
}

export function useRealtimeVoice({
  activeLesson,
  latestAssistantMessageContent,
  selectedRealtimeModel,
  selectedRealtimeOption,
  selectedRealtimeTransport,
  busyAction,
  setBusyAction,
  setError,
  flushAutoSave,
  chatRequestInFlightRef,
  onSubmitTranscript,
}: UseRealtimeVoiceOptions) {
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimePeerRef = useRef<RTCPeerConnection | null>(null);
  const realtimeChannelRef = useRef<RTCDataChannel | null>(null);
  const realtimeStreamRef = useRef<MediaStream | null>(null);
  const googleRealtimeSocketRef = useRef<WebSocket | null>(null);
  const googleAudioContextRef = useRef<AudioContext | null>(null);
  const googleAudioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const googleAudioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const googlePlaybackContextRef = useRef<AudioContext | null>(null);
  const googlePlaybackTimeRef = useRef(0);
  const googlePlaybackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const googleInputTranscriptRef = useRef("");
  const googleOutputTranscriptRef = useRef("");
  const openAIResponseInProgressRef = useRef(false);
  const realtimeLessonIdRef = useRef<string | null>(null);
  const realtimeClientSessionIdRef = useRef<string | null>(null);
  const realtimeLessonTitleRef = useRef<string | null>(null);
  const getRealtimeClientSessionId = useCallback(() => realtimeClientSessionIdRef.current, []);
  const getRealtimeLessonTitle = useCallback(() => realtimeLessonTitleRef.current, []);
  const { enqueueRealtimeLogEvent, flushRealtimeLogQueue, flushRealtimeLogQueueWithBeacon } = useRealtimeLogQueue({
    getClientSessionId: getRealtimeClientSessionId,
    getLessonTitle: getRealtimeLessonTitle,
  });

  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceStatusText, setVoiceStatusText] = useState(() => realtimeReadyStatus(selectedRealtimeOption));

  function stopGoogleQueuedPlayback() {
    googlePlaybackSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Already ended or never started.
      }
      try {
        source.disconnect();
      } catch {
        // Already disconnected.
      }
    });
    googlePlaybackSourcesRef.current.clear();
    const playbackContext = googlePlaybackContextRef.current;
    googlePlaybackTimeRef.current = playbackContext?.currentTime ?? 0;
  }

  function queueGooglePlayback(base64: string, mimeType?: string) {
    const playbackContext = googlePlaybackContextRef.current;
    if (!playbackContext) {
      return;
    }
    const source = playPcmBase64(base64, mimeType, playbackContext, googlePlaybackTimeRef);
    googlePlaybackSourcesRef.current.add(source);
    source.addEventListener(
      "ended",
      () => {
        googlePlaybackSourcesRef.current.delete(source);
      },
      { once: true }
    );
  }

  function resetOpenAIRemoteAudioPlayback() {
    const remoteAudio = remoteAudioRef.current;
    const remoteStream = remoteAudio?.srcObject;
    if (!remoteAudio || !remoteStream) {
      return;
    }
    remoteAudio.pause();
    remoteAudio.srcObject = null;
    remoteAudio.srcObject = remoteStream;
    void remoteAudio.play().catch(() => undefined);
  }

  function disposeRealtimeSession() {
    void flushRealtimeLogQueue();
    realtimeChannelRef.current?.close();
    realtimeChannelRef.current = null;
    googleRealtimeSocketRef.current?.close();
    googleRealtimeSocketRef.current = null;

    googleAudioProcessorRef.current?.disconnect();
    googleAudioProcessorRef.current = null;
    googleAudioSourceRef.current?.disconnect();
    googleAudioSourceRef.current = null;
    void googleAudioContextRef.current?.close().catch(() => undefined);
    googleAudioContextRef.current = null;
    stopGoogleQueuedPlayback();
    void googlePlaybackContextRef.current?.close().catch(() => undefined);
    googlePlaybackContextRef.current = null;
    googlePlaybackTimeRef.current = 0;
    googleInputTranscriptRef.current = "";
    googleOutputTranscriptRef.current = "";
    openAIResponseInProgressRef.current = false;

    if (realtimePeerRef.current) {
      realtimePeerRef.current.ontrack = null;
      realtimePeerRef.current.onconnectionstatechange = null;
      realtimePeerRef.current.close();
      realtimePeerRef.current = null;
    }

    realtimeStreamRef.current?.getTracks().forEach((track) => track.stop());
    realtimeStreamRef.current = null;

    if (remoteAudioRef.current) {
      remoteAudioRef.current.pause();
      remoteAudioRef.current.srcObject = null;
    }

    realtimeLessonIdRef.current = null;
    realtimeClientSessionIdRef.current = null;
    realtimeLessonTitleRef.current = null;
  }

  function stopRealtimeSession(statusText = "Voice Chatbot disconnected") {
    disposeRealtimeSession();
    window.speechSynthesis?.cancel();
    setVoiceActive(false);
    setVoiceStatusText(statusText);
    setBusyAction((current) => (current === "voice-connect" ? null : current));
  }

  const stopRealtimeSessionEvent = useEffectEvent((statusText: string) => {
    stopRealtimeSession(statusText);
  });

  function speakControlledChatbotMessage(content: string) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      return;
    }
    const text = content.trim();
    if (!text) {
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
  }

  function handleRealtimeUserTranscript(lessonId: string, transcript: string, eventType: string) {
    const normalized = transcript.trim();
    if (!normalized) {
      return;
    }
    enqueueRealtimeLogEvent(lessonId, "user", eventType, normalized);
    if (chatRequestInFlightRef.current) {
      setVoiceStatusText("Processing the previous voice input. Please wait.");
      return;
    }
    onSubmitTranscript(normalized);
  }

  function flushGoogleRealtimeTranscripts(lessonId: string) {
    const userTranscript = googleInputTranscriptRef.current.trim();
    const assistantTranscript = googleOutputTranscriptRef.current.trim();
    if (userTranscript) {
      handleRealtimeUserTranscript(lessonId, userTranscript, "google.input_transcription");
      googleInputTranscriptRef.current = "";
    }
    if (assistantTranscript) {
      enqueueRealtimeLogEvent(lessonId, "assistant", "google.output_transcription", assistantTranscript);
      googleOutputTranscriptRef.current = "";
    }
  }

  function beginGoogleAudioStreaming(socket: WebSocket, mediaStream: MediaStream, audioContext: AudioContext) {
    const source = audioContext.createMediaStreamSource(mediaStream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    source.connect(processor);
    processor.connect(audioContext.destination);
    googleAudioSourceRef.current = source;
    googleAudioProcessorRef.current = processor;
    processor.onaudioprocess = (event) => {
      if (socket.readyState !== WebSocket.OPEN) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const resampled = resampleLinear(input, audioContext.sampleRate, 16000);
      socket.send(
        JSON.stringify({
          realtimeInput: {
            audio: {
              mimeType: "audio/pcm;rate=16000",
              data: pcmFloatToBase64(resampled),
            },
          },
        })
      );
    };
  }

  function handleGoogleRealtimeMessage(message: GoogleRealtimeAudioMessage) {
    const lessonId = realtimeLessonIdRef.current;
    if (!lessonId) {
      return;
    }
    const serverContent = message.serverContent;
    if (!serverContent) {
      return;
    }
    const inputText = serverContent.inputTranscription?.text;
    if (inputText) {
      googleInputTranscriptRef.current += inputText;
    }
    if (serverContent.interrupted) {
      stopGoogleQueuedPlayback();
      googleOutputTranscriptRef.current = "";
      setVoiceStatusText("Interruption detected. Previous response stopped.");
    }
    const outputText = serverContent.outputTranscription?.text;
    if (outputText && !serverContent.interrupted) {
      googleOutputTranscriptRef.current += outputText;
    }
    serverContent.modelTurn?.parts?.forEach((part) => {
      const inlineData = part.inlineData;
      if (!inlineData?.data || serverContent.interrupted) {
        return;
      }
      queueGooglePlayback(inlineData.data, inlineData.mimeType);
    });
    if (serverContent.turnComplete) {
      flushGoogleRealtimeTranscripts(lessonId);
    }
  }

  async function startGoogleRealtimeSession(lesson: Lesson, mediaStream: MediaStream, clientSessionId: string) {
    const session = await api.createGoogleRealtimeSession(lesson.id, {
      latest_assistant_message: latestAssistantMessageContent,
      client_session_id: clientSessionId,
      realtime_model: selectedRealtimeModel,
    });
    const audioContext = new AudioContext();
    const playbackContext = new AudioContext();
    googleAudioContextRef.current = audioContext;
    googlePlaybackContextRef.current = playbackContext;
    googlePlaybackTimeRef.current = playbackContext.currentTime;
    await audioContext.resume();
    await playbackContext.resume();

    const socket = new WebSocket(getApiWebSocketUrl(session.websocket_url));
    googleRealtimeSocketRef.current = socket;
    await new Promise<void>((resolve, reject) => {
      let streamingStarted = false;
      let settled = false;
      const resolveStart = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve();
      };
      const rejectStart = (message: string) => {
        if (settled) {
          return;
        }
        settled = true;
        reject(new Error(message));
      };
      socket.onopen = () => {
        socket.send(JSON.stringify(session.setup));
      };
      socket.onerror = () => {
        rejectStart("Google Gemini Live WebSocket connection failed");
      };
      socket.onclose = (event) => {
        if (!streamingStarted) {
          rejectStart(
            `Google Gemini Live WebSocket closed before initialization (${event.code}${event.reason ? `: ${event.reason}` : ""})`
          );
        }
        if (googleRealtimeSocketRef.current === socket) {
          stopRealtimeSession("Google Gemini Live session ended");
        }
      };
      socket.onmessage = (event) => {
        void (async () => {
          try {
            const messageText = await websocketMessageText(event.data);
            const payload = JSON.parse(messageText) as GoogleRealtimeAudioMessage;
            if (payload.error) {
              const message = googleRealtimeErrorMessage(payload.error);
              if (!streamingStarted) {
                rejectStart(message);
                return;
              }
              stopRealtimeSession("Google Gemini Live session ended");
              setError(message);
              return;
            }
            if (payload.setupComplete && !streamingStarted) {
              streamingStarted = true;
              beginGoogleAudioStreaming(socket, mediaStream, audioContext);
              setVoiceActive(true);
              setBusyAction((current) => (current === "voice-connect" ? null : current));
              setVoiceStatusText(`Google Gemini Live connected. Voice: ${session.voice}`);
              resolveStart();
              return;
            }
            handleGoogleRealtimeMessage(payload);
          } catch {
            // ignore malformed realtime events
          }
        })();
      };
    });
  }

  async function handleVoiceToggle() {
    if (typeof window === "undefined") {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("Voice Chatbot disconnected manually");
      return;
    }
    if (!activeLesson) {
      return;
    }
    if (!selectedRealtimeOption?.enabled) {
      const message = realtimeUnavailableMessage(selectedRealtimeOption);
      setVoiceStatusText(message);
      setError(message);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser cannot access the microphone. Use a browser with microphone support and open the page through localhost or HTTPS.");
      return;
    }
    if (!(await flushAutoSave("voice"))) {
      return;
    }

    setBusyAction("voice-connect");
    const realtimeLabel = modelButtonLabel(selectedRealtimeOption ?? null, selectedRealtimeModel);
    setVoiceStatusText(`Connecting ${realtimeLabel}...`);
    setError(null);

    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      realtimeStreamRef.current = mediaStream;

      const clientSessionId = createClientSessionId("realtime");
      realtimeLessonIdRef.current = activeLesson.id;
      realtimeClientSessionIdRef.current = clientSessionId;
      realtimeLessonTitleRef.current = activeLesson.title;

      if (selectedRealtimeTransport === "gemini_live_websocket" || selectedRealtimeModel.provider === "google") {
        await startGoogleRealtimeSession(activeLesson, mediaStream, clientSessionId);
        return;
      }

      const peerConnection = new RTCPeerConnection();
      realtimePeerRef.current = peerConnection;

      mediaStream.getTracks().forEach((track) => {
        peerConnection.addTrack(track, mediaStream);
      });

      peerConnection.ontrack = (event) => {
        const [remoteStream] = event.streams;
        if (remoteAudioRef.current && remoteStream) {
          remoteAudioRef.current.srcObject = remoteStream;
          void remoteAudioRef.current.play().catch(() => undefined);
        }
      };

      peerConnection.onconnectionstatechange = () => {
        if (peerConnection.connectionState === "connected") {
          setVoiceActive(true);
          setVoiceStatusText(`${realtimeLabel} connected. Speech will enter the Chatbot workflow first.`);
          setBusyAction((current) => (current === "voice-connect" ? null : current));
          return;
        }
        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "closed" ||
          peerConnection.connectionState === "disconnected"
        ) {
          stopRealtimeSession("Voice session ended");
        }
      };

      const dataChannel = peerConnection.createDataChannel("oai-events");
      realtimeChannelRef.current = dataChannel;
      dataChannel.onmessage = (messageEvent) => {
        try {
          const payload = JSON.parse(messageEvent.data) as {
            type?: string;
            transcript?: string;
            name?: string;
            call_id?: string;
          };
          if (payload.type === "response.created") {
            openAIResponseInProgressRef.current = true;
          }
          if (payload.type === "response.done" || payload.type === "response.audio.done") {
            openAIResponseInProgressRef.current = false;
          }
          if (payload.type === "input_audio_buffer.speech_started") {
            if (openAIResponseInProgressRef.current && dataChannel.readyState === "open") {
              dataChannel.send(JSON.stringify({ type: "response.cancel" }));
              openAIResponseInProgressRef.current = false;
            }
            resetOpenAIRemoteAudioPlayback();
          }
          const lessonId = realtimeLessonIdRef.current;
          if (!lessonId || !payload.type) {
            return;
          }
          if (payload.type.includes("function_call") && payload.name) {
            enqueueRealtimeLogEvent(
              lessonId,
              "tool",
              payload.type,
              `${payload.name}${payload.call_id ? ` (${payload.call_id})` : ""}`
            );
          }
          if (!payload.transcript) {
            return;
          }
          if (
            payload.type === "conversation.item.input_audio_transcription.completed" ||
            payload.type === "conversation.item.input_audio_transcription.done"
          ) {
            handleRealtimeUserTranscript(lessonId, payload.transcript, payload.type);
          }
          if (payload.type === "response.audio_transcript.done") {
            enqueueRealtimeLogEvent(lessonId, "assistant", payload.type, payload.transcript);
          }
        } catch {
          // ignore
        }
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      const realtimeResponse = await api.connectRealtime(activeLesson.id, {
        offer_sdp: offer.sdp ?? "",
        latest_assistant_message: latestAssistantMessageContent,
        client_session_id: clientSessionId,
        realtime_model: selectedRealtimeModel,
      });
      if (realtimeResponse.client_session_id) {
        realtimeClientSessionIdRef.current = realtimeResponse.client_session_id;
      }

      await peerConnection.setRemoteDescription({
        type: "answer",
        sdp: realtimeResponse.answer_sdp,
      });

      setVoiceStatusText(
        `${PROVIDER_LABELS[realtimeResponse.provider]} ${realtimeResponse.model} is ready${
          realtimeResponse.tools_enabled ? ", Chatbot tools enabled" : ", controlled transcription active"
        }`
      );
    } catch (voiceError) {
      stopRealtimeSession("Voice connection failed");
      setError(realtimeConnectionErrorMessage(voiceError, selectedRealtimeModel));
    }
  }

  const scheduleRealtimeLogFlushEffectEvent = useEffectEvent(() => {
    void flushRealtimeLogQueue();
  });

  const flushRealtimeLogQueueWithBeaconEffectEvent = useEffectEvent(() => {
    flushRealtimeLogQueueWithBeacon();
  });

  const disposeRealtimeSessionEffectEvent = useEffectEvent(() => {
    disposeRealtimeSession();
  });

  useEffect(() => {
    return () => {
      flushRealtimeLogQueueWithBeaconEffectEvent();
      disposeRealtimeSessionEffectEvent();
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      scheduleRealtimeLogFlushEffectEvent();
    }, 2000);

    function handlePageHide() {
      flushRealtimeLogQueueWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
    };
  }, []);

  useEffect(() => {
    if (!realtimeLessonIdRef.current || realtimeLessonIdRef.current === activeLesson?.id) {
      return;
    }
    stopRealtimeSessionEvent("Course changed. Voice session disconnected automatically.");
  }, [activeLesson?.id]);

  const idleVoiceStatusText =
    !voiceActive && busyAction !== "voice-connect" && isAutomaticRealtimeStatus(voiceStatusText)
      ? realtimeReadyStatus(selectedRealtimeOption)
      : voiceStatusText;

  return {
    remoteAudioRef,
    voiceActive,
    voiceStatusText: idleVoiceStatusText,
    setVoiceStatusText,
    handleVoiceToggle,
    stopRealtimeSession,
    speakControlledChatbotMessage,
  };
}

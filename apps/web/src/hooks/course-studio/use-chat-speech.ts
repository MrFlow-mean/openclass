"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import type { ChatMessage } from "@/components/course-studio/history-utils";
import { synthesizeSpeech } from "@/lib/speech-api";
import { prepareSpeechText } from "@/lib/speech-text";

const AUTO_SPEECH_STORAGE_KEY = "openclass:studio:auto-speak-chat";
const AUTO_SPEECH_CHANGE_EVENT = "openclass:auto-speech-change";

type SpeechPlaybackStatus = "idle" | "loading" | "playing" | "error";

type UseChatSpeechOptions = {
  lessonId: string | null;
  messages: ChatMessage[];
};

function subscribeToAutoSpeechPreference(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(AUTO_SPEECH_CHANGE_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(AUTO_SPEECH_CHANGE_EVENT, callback);
  };
}

function readAutoSpeechPreference() {
  try {
    return window.localStorage.getItem(AUTO_SPEECH_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function useChatSpeech({ lessonId, messages }: UseChatSpeechOptions) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const requestSequenceRef = useRef(0);
  const trackedLessonIdRef = useRef<string | null>(null);
  const latestSeenAssistantIdRef = useRef<string | null>(null);
  const autoSpeakEnabled = useSyncExternalStore(
    subscribeToAutoSpeechPreference,
    readAutoSpeechPreference,
    () => false
  );
  const [status, setStatus] = useState<SpeechPlaybackStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("自动播报已关闭");

  const latestAssistantMessage = useMemo(
    () =>
      [...messages]
        .reverse()
        .find((message) => message.role === "assistant" && message.status === "ready" && message.content.trim()),
    [messages]
  );

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.onplay = null;
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  const stopSpeech = useCallback(() => {
    requestSequenceRef.current += 1;
    requestRef.current?.abort();
    requestRef.current = null;
    releaseAudio();
    setStatus("idle");
    setStatusMessage(autoSpeakEnabled ? "新回复会自动用 TTS 模型播报" : "自动播报已关闭");
  }, [autoSpeakEnabled, releaseAudio]);

  const speakMessage = useCallback(
    async (content: string) => {
      const speechText = prepareSpeechText(content);
      if (!speechText) {
        return;
      }

      const requestSequence = requestSequenceRef.current + 1;
      requestSequenceRef.current = requestSequence;
      requestRef.current?.abort();
      releaseAudio();
      const controller = new AbortController();
      requestRef.current = controller;
      setStatus("loading");
      setStatusMessage("TTS 模型正在生成音频…");

      try {
        const response = await synthesizeSpeech(speechText, controller.signal);
        if (requestSequenceRef.current !== requestSequence) {
          return;
        }
        requestRef.current = null;
        const audioUrl = URL.createObjectURL(response.audio);
        const audio = new Audio(audioUrl);
        audioUrlRef.current = audioUrl;
        audioRef.current = audio;
        const modelLabel = [response.model, response.voice].filter(Boolean).join(" · ");
        audio.onplay = () => {
          setStatus("playing");
          setStatusMessage(modelLabel ? `正在播报 · ${modelLabel}` : "正在播报");
        };
        audio.onended = () => {
          releaseAudio();
          setStatus("idle");
          setStatusMessage(autoSpeakEnabled ? "播报完成，等待下一条回复" : "播报完成");
        };
        audio.onerror = () => {
          releaseAudio();
          setStatus("error");
          setStatusMessage("浏览器没有成功播放这段音频");
        };
        await audio.play();
      } catch (error) {
        if (controller.signal.aborted || requestSequenceRef.current !== requestSequence) {
          return;
        }
        requestRef.current = null;
        releaseAudio();
        setStatus("error");
        setStatusMessage(error instanceof Error ? error.message : "语音播报失败");
      }
    },
    [autoSpeakEnabled, releaseAudio]
  );

  const toggleAutoSpeak = useCallback(() => {
    const nextEnabled = !autoSpeakEnabled;
    try {
      window.localStorage.setItem(AUTO_SPEECH_STORAGE_KEY, nextEnabled ? "true" : "false");
    } catch {
      // The current page can still use manual playback when browser storage is unavailable.
    }
    window.dispatchEvent(new Event(AUTO_SPEECH_CHANGE_EVENT));
    if (nextEnabled) {
      setStatus("idle");
      setStatusMessage("新回复会自动用 TTS 模型播报");
      return;
    }
    requestSequenceRef.current += 1;
    requestRef.current?.abort();
    requestRef.current = null;
    releaseAudio();
    setStatus("idle");
    setStatusMessage("自动播报已关闭");
  }, [autoSpeakEnabled, releaseAudio]);

  useEffect(() => {
    const latestId = latestAssistantMessage?.id ?? null;
    if (trackedLessonIdRef.current !== lessonId) {
      requestSequenceRef.current += 1;
      requestRef.current?.abort();
      requestRef.current = null;
      releaseAudio();
      trackedLessonIdRef.current = lessonId;
      latestSeenAssistantIdRef.current = latestId;
      setStatus("idle");
      setStatusMessage(autoSpeakEnabled ? "新回复会自动用 TTS 模型播报" : "自动播报已关闭");
      return;
    }
    if (!latestAssistantMessage || latestSeenAssistantIdRef.current === latestId) {
      return;
    }
    latestSeenAssistantIdRef.current = latestId;
    if (autoSpeakEnabled) {
      const timeoutId = window.setTimeout(() => {
        void speakMessage(latestAssistantMessage.content);
      }, 0);
      return () => window.clearTimeout(timeoutId);
    }
  }, [autoSpeakEnabled, latestAssistantMessage, lessonId, releaseAudio, speakMessage]);

  useEffect(() => {
    return () => {
      requestSequenceRef.current += 1;
      requestRef.current?.abort();
      releaseAudio();
    };
  }, [releaseAudio]);

  return {
    autoSpeakEnabled,
    isSpeechActive: status === "loading" || status === "playing",
    speechStatusText:
      status === "idle" && autoSpeakEnabled && statusMessage === "自动播报已关闭"
        ? "新回复会自动用 TTS 模型播报"
        : statusMessage,
    speakMessage,
    stopSpeech,
    toggleAutoSpeak,
  };
}

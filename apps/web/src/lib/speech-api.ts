import { getApiBase, readEffectiveAuthToken } from "@/lib/api";

export type SpeechAudioResponse = {
  audio: Blob;
  model: string | null;
  voice: string | null;
};

export async function synthesizeSpeech(input: string, signal?: AbortSignal): Promise<SpeechAudioResponse> {
  const token = readEffectiveAuthToken();
  const response = await fetch(`${getApiBase()}/api/speech`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ input }),
    cache: "no-store",
    signal,
  });

  if (!response.ok) {
    let message = "语音模型没有成功生成音频";
    const body = await response.text();
    if (body.trim()) {
      try {
        const payload = JSON.parse(body) as { detail?: unknown };
        if (typeof payload.detail === "string" && payload.detail.trim()) {
          message = payload.detail.trim();
        }
      } catch {
        message = body.trim();
      }
    }
    throw new Error(message);
  }

  return {
    audio: await response.blob(),
    model: response.headers.get("X-Speech-Model"),
    voice: response.headers.get("X-Speech-Voice"),
  };
}

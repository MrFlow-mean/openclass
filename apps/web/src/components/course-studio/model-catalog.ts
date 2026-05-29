import type { AIModelCatalog, AIModelOption, AIModelSelection } from "@/types";

export type GoogleRealtimeAudioMessage = {
  setupComplete?: Record<string, unknown>;
  error?: {
    code?: number;
    message?: string;
    status?: string;
  };
  serverContent?: {
    modelTurn?: {
      parts?: Array<{
        inlineData?: {
          mimeType?: string;
          data?: string;
        };
        text?: string;
      }>;
    };
    inputTranscription?: {
      text?: string;
    };
    outputTranscription?: {
      text?: string;
    };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
};

export const FALLBACK_MODEL_CATALOG: AIModelCatalog = {
  text: [
    {
      provider: "openai",
      model: "gpt-5.5",
      label: "GPT-5.5",
      capability: "text",
      enabled: true,
      configured: true,
      default: true,
    },
  ],
  realtime: [],
  defaults: {
    text: { provider: "openai", model: "gpt-5.5" },
    realtime: { provider: "openai", model: "gpt-realtime-2" },
  },
};

export const PROVIDER_LABELS: Record<AIModelSelection["provider"], string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  deepseek: "DeepSeek",
  kimi: "Kimi",
  minimax: "MiniMax",
  openai_compatible: "OpenAI compatible",
  anthropic_compatible: "Anthropic compatible",
};

export const TEXT_MODEL_STORAGE_KEY = "blackboard-ai:selected-text-model";
export const REALTIME_MODEL_STORAGE_KEY = "blackboard-ai:selected-realtime-model";

const DISABLED_TEXT_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();
const DISABLED_REALTIME_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();

export function modelSelectionKey(selection: AIModelSelection): string {
  return `${selection.provider}:${selection.model}`;
}

export function modelOptionKey(option: AIModelOption): string {
  return `${option.provider}:${option.model}`;
}

export function findModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  if (!selection) {
    return null;
  }
  return options.find((option) => modelOptionKey(option) === modelSelectionKey(selection)) ?? null;
}

function findEnabledModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  const option = findModelOption(options, selection);
  return option?.enabled ? option : null;
}

export function normalizeCourseStudioModelCatalog(catalog: AIModelCatalog): AIModelCatalog {
  return {
    ...catalog,
    text: catalog.text.map((option) =>
      DISABLED_TEXT_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
    realtime: catalog.realtime.map((option) =>
      DISABLED_REALTIME_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
  };
}

export function modelButtonLabel(option: AIModelOption | null, fallback: AIModelSelection | null): string {
  if (option) {
    return option.label;
  }
  if (!fallback) {
    return "Not selected";
  }
  return `${PROVIDER_LABELS[fallback.provider]} ${fallback.model}`;
}

export function optionToSelection(option: AIModelOption): AIModelSelection {
  return {
    provider: option.provider,
    model: option.model,
  };
}

function isModelSelection(value: unknown): value is AIModelSelection {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<AIModelSelection>;
  return (
    typeof candidate.provider === "string" &&
    candidate.provider in PROVIDER_LABELS &&
    typeof candidate.model === "string" &&
    candidate.model.trim().length > 0
  );
}

export function readStoredModelSelection(key: string): AIModelSelection | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    return isModelSelection(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export async function websocketMessageText(data: MessageEvent["data"]): Promise<string> {
  if (typeof data === "string") {
    return data;
  }
  if (data instanceof Blob) {
    return data.text();
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder().decode(data);
  }
  if (ArrayBuffer.isView(data)) {
    return new TextDecoder().decode(data);
  }
  return String(data);
}

export function googleRealtimeErrorMessage(error: GoogleRealtimeAudioMessage["error"]): string {
  const rawMessage = error?.message?.trim() ?? "";
  const status = error?.status?.trim() ?? "";
  const lowerMessage = rawMessage.toLowerCase();
  const lowerStatus = status.toLowerCase();

  if (error?.code === 401 || lowerStatus.includes("unauthenticated")) {
    return "Google Gemini Live authentication failed. Check whether the unified model API key is correct.";
  }
  if (error?.code === 403 || lowerStatus.includes("permission") || lowerMessage.includes("permission denied")) {
    return "Google Gemini Live permission was denied. Check that the Google API key has Gemini API enabled and can use the Live API.";
  }
  if (error?.code === 429 || lowerStatus.includes("quota") || lowerMessage.includes("quota")) {
    return "Google Gemini Live quota is exhausted or requests are too frequent. Try again later or check Google API quota.";
  }
  if (rawMessage) {
    return `Google Gemini Live connection failed: ${rawMessage}`;
  }
  return "Google Gemini Live connection failed.";
}

export function realtimeConnectionErrorMessage(error: unknown, selection: AIModelSelection): string {
  const errorName = typeof error === "object" && error && "name" in error ? String(error.name) : "";
  const rawMessage = error instanceof Error ? error.message.trim() : "";
  const lowerMessage = rawMessage.toLowerCase();

  if (
    errorName === "NotAllowedError" ||
    errorName === "SecurityError" ||
    lowerMessage === "permission denied" ||
    lowerMessage.includes("permission dismissed")
  ) {
    return "Microphone permission was denied. Allow microphone access in the browser address bar; if using the local launcher, reopen it or choose direct frontend access; outside localhost, open the page through HTTPS.";
  }
  if (errorName === "NotFoundError" || lowerMessage.includes("requested device not found")) {
    return "No available microphone was found. Connect or enable a microphone and try again.";
  }
  if (errorName === "NotReadableError" || lowerMessage.includes("could not start audio source")) {
    return "The microphone is temporarily unavailable and may be used by another app. Close the other app and try again.";
  }
  if (rawMessage) {
    return rawMessage;
  }
  return `Failed to connect ${PROVIDER_LABELS[selection.provider]} realtime voice`;
}

export function persistModelSelection(key: string, selection: AIModelSelection) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(selection));
}

export function resolveModelSelection(
  options: AIModelOption[],
  preferred: AIModelSelection | null,
  fallback: AIModelSelection
): AIModelSelection {
  if (preferred && findEnabledModelOption(options, preferred)) {
    return preferred;
  }
  if (findEnabledModelOption(options, fallback)) {
    return fallback;
  }
  const defaultOption =
    options.find((option) => option.default && option.enabled) ?? options.find((option) => option.enabled) ?? options[0];
  return defaultOption ? optionToSelection(defaultOption) : fallback;
}

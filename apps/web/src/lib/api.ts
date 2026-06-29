import type {
  AIModelCatalog,
  AdminOverview,
  AuthProviderView,
  AuthSessionResponse,
  ChatRequestPayload,
  ChatResponse,
  CodexLoginStartResponse,
  CodexLoginStatusResponse,
  CodexProviderStatus,
  GoogleRealtimeSessionPayload,
  GoogleRealtimeSessionResponse,
  RealtimeConnectPayload,
  RealtimeConnectResponse,
  RealtimeEventLogPayload,
  UserView,
} from "@/types";

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
export const OPENCLASS_AUTH_TOKEN_STORAGE_KEY = "openclass.auth.token";
export const OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY = "openclass.guest.auth.token";
let guestAuthToken: string | null = null;

function readCookie(name: string) {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
  if (!cookie) {
    return null;
  }
  try {
    return decodeURIComponent(cookie);
  } catch {
    return cookie;
  }
}

function clearCookie(name: string) {
  if (typeof document === "undefined") {
    return;
  }
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

function readSessionToken(name: string) {
  if (typeof window === "undefined") {
    return null;
  }
  return window.sessionStorage.getItem(name);
}

function storeSessionToken(name: string, token: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(name, token);
}

function clearSessionToken(name: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(name);
}

export function getApiBase() {
  if (configuredApiBase) {
    return configuredApiBase;
  }
  if (typeof window !== "undefined" && window.location.hostname) {
    if (window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
      return window.location.origin;
    }
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export function readAuthToken() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
}

export function readGuestAuthToken() {
  if (guestAuthToken) {
    return guestAuthToken;
  }
  guestAuthToken = readSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY) || readCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  return guestAuthToken;
}

export function readEffectiveAuthToken() {
  return readAuthToken() || readGuestAuthToken();
}

export function storeAuthToken(token: string) {
  if (typeof window === "undefined") {
    return;
  }
  guestAuthToken = null;
  clearSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.setItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY, token);
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${OPENCLASS_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; Max-Age=2592000; SameSite=Lax${secure}`;
}

export function storeGuestAuthToken(token: string) {
  guestAuthToken = token;
  if (typeof window === "undefined") {
    return;
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  storeSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  document.cookie = `${OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; SameSite=Lax${secure}`;
}

export function clearAuthToken() {
  guestAuthToken = null;
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  clearSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
}

function authHeaders(headers?: HeadersInit) {
  const nextHeaders = new Headers(headers);
  if (!nextHeaders.has("Authorization")) {
    const token = readEffectiveAuthToken();
    if (token) {
      nextHeaders.set("Authorization", `Bearer ${token}`);
    }
  }
  return nextHeaders;
}

function withAuthTokenQuery(url: string) {
  if (typeof window === "undefined") {
    return url;
  }
  const token = readEffectiveAuthToken();
  if (!token) {
    return url;
  }
  const nextUrl = new URL(url, window.location.href);
  nextUrl.searchParams.set("access_token", token);
  return nextUrl.toString();
}

export function getApiWebSocketUrl(pathOrUrl: string) {
  if (pathOrUrl.startsWith("ws://") || pathOrUrl.startsWith("wss://")) {
    return withAuthTokenQuery(pathOrUrl);
  }

  const apiBase = getApiBase();
  const baseUrl = new URL(apiBase);
  baseUrl.protocol = baseUrl.protocol === "https:" ? "wss:" : "ws:";
  return withAuthTokenQuery(new URL(pathOrUrl, baseUrl).toString());
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && !(init?.body instanceof FormData) && !(init?.body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
  }
  if (typeof window !== "undefined" && !headers.has("Authorization")) {
    const token = readEffectiveAuthToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed with ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      }
    } catch {
      // Keep the raw response text for non-JSON errors.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

type ChatStreamHandlers = {
  onPhase?: (label: string) => void;
  onChatDelta?: (delta: string) => void;
  onFinal?: (response: ChatResponse) => void;
};

export type ChatStreamFailureKind = "http" | "sse" | "missing_final";

export class ChatStreamTransportError extends Error {
  kind: ChatStreamFailureKind;
  status?: number;

  constructor(message: string, kind: ChatStreamFailureKind, status?: number) {
    super(message);
    this.name = "ChatStreamTransportError";
    this.kind = kind;
    this.status = status;
  }
}

export function isMissingChatStreamFinalError(error: unknown) {
  return error instanceof ChatStreamTransportError && error.kind === "missing_final";
}

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  return { event, data: dataLines.join("\n") };
}

function handleChatStreamBlock(block: string, handlers: ChatStreamHandlers) {
  const parsed = parseSseBlock(block);
  if (!parsed) {
    return;
  }
  const payload = JSON.parse(parsed.data) as Record<string, unknown>;
  if (parsed.event === "heartbeat") {
    return;
  }
  if (parsed.event === "phase") {
    const label = typeof payload.label === "string" ? payload.label : "";
    if (label) {
      handlers.onPhase?.(label);
    }
    return;
  }
  if (parsed.event === "chat_delta") {
    const delta = typeof payload.delta === "string" ? payload.delta : "";
    if (delta) {
      handlers.onChatDelta?.(delta);
    }
    return;
  }
  if (parsed.event === "final") {
    handlers.onFinal?.(payload as unknown as ChatResponse);
    return;
  }
  if (parsed.event === "error") {
    const message = typeof payload.message === "string" ? payload.message : "聊天失败";
    throw new ChatStreamTransportError(message, "sse");
  }
}

async function streamRequest(path: string, payload: unknown, handlers: ChatStreamHandlers): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
    });
  } catch (fetchError) {
    const message = fetchError instanceof Error ? fetchError.message : "聊天流连接失败";
    throw new ChatStreamTransportError(message, "missing_final");
  }
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new ChatStreamTransportError(text || `Request failed with ${response.status}`, "http", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: ChatResponse | null = null;
  const streamHandlers: ChatStreamHandlers = {
    ...handlers,
    onFinal(responsePayload) {
      finalResponse = responsePayload;
      handlers.onFinal?.(responsePayload);
    },
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (block) {
          handleChatStreamBlock(block, streamHandlers);
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) {
        break;
      }
    }
  } catch (streamError) {
    if (streamError instanceof ChatStreamTransportError) {
      throw streamError;
    }
    const message = streamError instanceof Error ? streamError.message : "聊天流连接中断";
    throw new ChatStreamTransportError(message, "missing_final");
  }
  const rest = buffer.trim();
  if (rest) {
    handleChatStreamBlock(rest, streamHandlers);
  }
  if (!finalResponse) {
    throw new ChatStreamTransportError("聊天流没有返回最终结果", "missing_final");
  }
  return finalResponse;
}

export const api = {
  register(identifier: string, password: string) {
    return request<AuthSessionResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ identifier, password, guest_token: readGuestAuthToken() }),
    });
  },
  login(identifier: string, password: string) {
    return request<AuthSessionResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password, guest_token: readGuestAuthToken() }),
    });
  },
  startGuestSession() {
    return request<AuthSessionResponse>("/api/auth/guest", {
      method: "POST",
    });
  },
  getCurrentUser() {
    return request<UserView>("/api/auth/me");
  },
  getAuthProviders() {
    return request<AuthProviderView[]>("/api/auth/providers");
  },
  getAdminOverview() {
    return request<AdminOverview>("/api/admin/overview");
  },
  getAIModels() {
    return request<AIModelCatalog>("/api/ai-models");
  },
  getCodexStatus(includeRateLimits = false) {
    const query = includeRateLimits ? "?include_rate_limits=true" : "";
    return request<CodexProviderStatus>(`/api/codex/status${query}`);
  },
  startCodexDeviceLogin() {
    return request<CodexLoginStartResponse>("/api/codex/login/device", {
      method: "POST",
    });
  },
  getCodexLoginStatus(loginId: string) {
    return request<CodexLoginStatusResponse>(`/api/codex/login/${encodeURIComponent(loginId)}`);
  },
  cancelCodexLogin(loginId: string) {
    return request<CodexLoginStatusResponse>(`/api/codex/login/${encodeURIComponent(loginId)}/cancel`, {
      method: "POST",
    });
  },
  logoutCodex() {
    return request<{ ok: boolean }>("/api/codex/logout", {
      method: "POST",
    });
  },
  chatOnLesson(lessonId: string, payload: ChatRequestPayload) {
    return request<ChatResponse>(`/api/lessons/${lessonId}/chat`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  streamChatOnLesson(lessonId: string, payload: ChatRequestPayload, handlers: ChatStreamHandlers) {
    return streamRequest(`/api/lessons/${lessonId}/chat/stream`, payload, handlers);
  },
  connectRealtime(lessonId: string, payload: RealtimeConnectPayload) {
    return request<RealtimeConnectResponse>(`/api/lessons/${lessonId}/realtime/connect`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createGoogleRealtimeSession(lessonId: string, payload: GoogleRealtimeSessionPayload) {
    return request<GoogleRealtimeSessionResponse>(`/api/lessons/${lessonId}/realtime/google/session`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  logRealtimeEvent(lessonId: string, payload: RealtimeEventLogPayload) {
    return request<{ status: string }>(`/api/lessons/${lessonId}/realtime/events`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  logRealtimeEventBeacon(lessonId: string, payload: RealtimeEventLogPayload) {
    if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
      return false;
    }
    const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
    return navigator.sendBeacon(
      withAuthTokenQuery(`${getApiBase()}/api/lessons/${lessonId}/realtime/events`),
      blob
    );
  },
};

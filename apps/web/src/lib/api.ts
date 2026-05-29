import type {
  AIModelCatalog,
  AdminOverview,
  AuthProviderView,
  AuthSessionResponse,
  ChatRequestPayload,
  ChatResponse,
  CoursePackage,
  DocumentAIEditPayload,
  DocumentSavePayload,
  GoogleRealtimeSessionPayload,
  GoogleRealtimeSessionResponse,
  MergeBranchChoice,
  MergeBranchPreviewResponse,
  RealtimeConnectPayload,
  RealtimeConnectResponse,
  RealtimeEventLogPayload,
  ScopeAction,
  WorkspaceState,
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
  onDocumentDelta?: (delta: string) => void;
  onFinal?: (response: ChatResponse) => void;
};

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
  if (parsed.event === "document_delta") {
    const delta = typeof payload.delta === "string" ? payload.delta : "";
    if (delta) {
      handlers.onDocumentDelta?.(delta);
    }
    return;
  }
  if (parsed.event === "final") {
    handlers.onFinal?.(payload as unknown as ChatResponse);
    return;
  }
  if (parsed.event === "error") {
    const message = typeof payload.message === "string" ? payload.message : "Chat failed";
    throw new Error(message);
  }
}

async function streamRequest(path: string, payload: unknown, handlers: ChatStreamHandlers): Promise<ChatResponse> {
  const response = await fetch(`${getApiBase()}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
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
  const rest = buffer.trim();
  if (rest) {
    handleChatStreamBlock(rest, streamHandlers);
  }
  if (!finalResponse) {
    throw new Error("The chat stream did not return a final result");
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
  getWorkspace() {
    return request<WorkspaceState>("/api/workspace");
  },
  createPackage(title: string, summary = "") {
    return request<WorkspaceState>("/api/packages", {
      method: "POST",
      body: JSON.stringify({
        title,
        summary,
      }),
    });
  },
  openPackage(packageId: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}/open`, {
      method: "POST",
    });
  },
  renamePackage(packageId: string, title: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}`, {
      method: "POST",
      body: JSON.stringify({
        title,
      }),
    });
  },
  deletePackage(packageId: string) {
    return request<WorkspaceState>(`/api/packages/${packageId}/delete`, {
      method: "POST",
    });
  },
  moveLesson(lessonId: string, targetPackageId: string) {
    return request<WorkspaceState>(`/api/lessons/${lessonId}/move`, {
      method: "POST",
      body: JSON.stringify({
        target_package_id: targetPackageId,
      }),
    });
  },
  deleteLesson(lessonId: string) {
    return request<WorkspaceState>(`/api/lessons/${lessonId}/delete`, {
      method: "POST",
    });
  },
  getCoursePackage() {
    return request<CoursePackage>("/api/course-package");
  },
  generateLesson(
    topic: string,
    options: {
      branchFromLessonId?: string | null;
      startBlank?: boolean;
      targetPackageId?: string | null;
    } = {}
  ) {
    return request<CoursePackage>("/api/lessons/generate", {
      method: "POST",
      body: JSON.stringify({
        topic,
        branch_from_lesson_id: options.branchFromLessonId ?? null,
        target_package_id: options.targetPackageId ?? null,
        start_blank: options.startBlank ?? false,
      }),
    });
  },
  saveDocument(lessonId: string, payload: DocumentSavePayload) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/document/save`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  saveDocumentBeacon(lessonId: string, payload: DocumentSavePayload) {
    if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") {
      return false;
    }
    const blob = new Blob([JSON.stringify(payload)], { type: "text/plain;charset=UTF-8" });
    return navigator.sendBeacon(
      withAuthTokenQuery(`${getApiBase()}/api/lessons/${lessonId}/document/save-beacon`),
      blob
    );
  },
  saveDocumentKeepalive(lessonId: string, payload: DocumentSavePayload) {
    return fetch(`${getApiBase()}/api/lessons/${lessonId}/document/save`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
      keepalive: true,
    });
  },
  aiEditDocument(lessonId: string, payload: DocumentAIEditPayload) {
    return request<ChatResponse>(`/api/lessons/${lessonId}/document/ai-edit`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  async importDocx(lessonId: string, file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/import-docx`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed with ${response.status}`);
    }
    return response.json() as Promise<CoursePackage>;
  },
  async exportDocx(lessonId: string) {
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/export-docx`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Export failed with ${response.status}`);
    }
    return response.blob();
  },
  createBranch(lessonId: string, name: string, fromCommitId?: string | null) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/branches`, {
      method: "POST",
      body: JSON.stringify({ name, from_commit_id: fromCommitId ?? null }),
    });
  },
  previewBranchMerge(lessonId: string, sourceBranch: string, targetBranch?: string | null) {
    return request<MergeBranchPreviewResponse>(`/api/lessons/${lessonId}/branches/merge-preview`, {
      method: "POST",
      body: JSON.stringify({ source_branch: sourceBranch, target_branch: targetBranch ?? null }),
    });
  },
  mergeBranch(
    lessonId: string,
    payload: {
      source_branch: string;
      target_branch?: string | null;
      expected_target_head_commit_id: string;
      expected_source_head_commit_id: string;
      document_choice: MergeBranchChoice;
      requirements_choice: MergeBranchChoice;
      session_choice: MergeBranchChoice;
    }
  ) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/branches/merge`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  switchBranch(lessonId: string, name: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/branches/checkout`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  restoreCommit(lessonId: string, commitId: string, label = "Restore snapshot") {
    return request<CoursePackage>(`/api/lessons/${lessonId}/restore`, {
      method: "POST",
      body: JSON.stringify({ commit_id: commitId, label }),
    });
  },
  reorderWorkspace(orderedLessonIds: string[], activeLessonId?: string | null) {
    return request<CoursePackage>("/api/workspace/reorder", {
      method: "POST",
      body: JSON.stringify({
        ordered_lesson_ids: orderedLessonIds,
        active_lesson_id: activeLessonId ?? null,
      }),
    });
  },
  openLesson(lessonId: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/open`, {
      method: "POST",
    });
  },
  closeLesson(lessonId: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/close`, {
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
  async uploadResource(file: File, lessonId?: string | null) {
    const formData = new FormData();
    formData.append("file", file);
    if (lessonId) {
      formData.append("lesson_id", lessonId);
    }
    const response = await fetch(`${getApiBase()}/api/resources/upload`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed with ${response.status}`);
    }
    return response.json() as Promise<CoursePackage>;
  },
  deleteResource(resourceId: string, lessonId?: string | null) {
    const query = lessonId ? `?lesson_id=${encodeURIComponent(lessonId)}` : "";
    return request<CoursePackage>(`/api/resources/${resourceId}/delete${query}`, {
      method: "POST",
    });
  },
  runScopeAction(
    lessonId: string,
    message: string,
    selection: ChatRequestPayload["selection"],
    scopeAction: ScopeAction,
    resourceChapterId?: string | null
  ) {
    return api.chatOnLesson(lessonId, {
      message,
      selection,
      scope_action: scopeAction,
      resource_chapter_id: resourceChapterId ?? null,
    });
  },
};

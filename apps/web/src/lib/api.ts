import type {
  AIModelCatalog,
  AdminOverview,
  AuthProviderView,
  AuthSessionResponse,
  BoardTaskUpdateStreamPayload,
  ChatRequestPayload,
  ChatResponse,
  CoursePackage,
  CodexLoginStartResponse,
  CodexLoginStatusResponse,
  CodexProviderStatus,
  DocumentAIEditPayload,
  DocumentSavePayload,
  GoogleRealtimeSessionPayload,
  GoogleRealtimeSessionResponse,
  RealtimeConnectPayload,
  RealtimeConnectResponse,
  RealtimeEventLogPayload,
  ResearchArtifact,
  ResearchArtifactCreate,
  ResearchAskRequest,
  ResearchAskResponse,
  ResearchCapabilities,
  ResearchChatMessage,
  ResearchChatRequest,
  ResearchChatResponse,
  ResearchChatThread,
  ResearchChatThreadCreate,
  ResearchChatThreadUpdate,
  ResearchNote,
  ResearchNoteCreate,
  ResearchNoteUpdate,
  ResearchEpisodeProfile,
  ResearchEpisodeProfileCreate,
  ResearchEpisodeProfileUpdate,
  ResearchSearchRequest,
  ResearchSearchResponse,
  ResearchSpeakerProfile,
  ResearchSpeakerProfileCreate,
  ResearchSpeakerProfileUpdate,
  ResearchTransformation,
  ResearchTransformationCreate,
  ResearchTransformationRun,
  ResearchTransformationUpdate,
  RequirementUpdateStreamPayload,
  ScopeAction,
  EvidenceBundle,
  EvidenceConfirmationResult,
  SourceIngestionRecord,
  SourceContentView,
  SourceStructureView,
  WorkspaceState,
  UserView,
} from "@/types";

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
export const OPENCLASS_AUTH_TOKEN_STORAGE_KEY = "openclass.auth.token";
export const OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY = "openclass.guest.auth.token";
export const OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY = "openclass.connected-guest.auth.token";
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
  guestAuthToken =
    readSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY) ||
    (typeof window === "undefined" ? null : window.localStorage.getItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY)) ||
    readCookie(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY);
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
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
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
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
  clearCookie(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  storeSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  document.cookie = `${OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; SameSite=Lax${secure}`;
}

export function persistConnectedGuestAuthToken() {
  const token = readGuestAuthToken();
  if (!token || typeof window === "undefined") {
    return;
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  window.localStorage.setItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  storeSessionToken(OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY, token);
  document.cookie = `${OPENCLASS_GUEST_AUTH_TOKEN_STORAGE_KEY}=${encodeURIComponent(token)}; Path=/; Max-Age=2592000; SameSite=Lax${secure}`;
}

export function clearAuthToken() {
  guestAuthToken = null;
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(OPENCLASS_AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(OPENCLASS_CONNECTED_GUEST_AUTH_TOKEN_STORAGE_KEY);
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

async function responseErrorMessage(response: Response, fallback: string) {
  const text = await response.text();
  let message = text || fallback;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      message = parsed.detail;
    }
  } catch {
    // Keep the raw response text for non-JSON errors.
  }
  return message;
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
    const message = await responseErrorMessage(response, `Request failed with ${response.status}`);
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

type ChatStreamHandlers = {
  onPhase?: (label: string) => void;
  onAgentActivity?: (event: NonNullable<ChatResponse["agent_activity"]>[number]) => void;
  onChatDelta?: (delta: string) => void;
  onDocumentDelta?: (delta: string) => void;
  onRequirementUpdate?: (payload: RequirementUpdateStreamPayload) => void;
  onBoardTaskUpdate?: (payload: BoardTaskUpdateStreamPayload) => void;
  onFinal?: (response: ChatResponse) => void;
};

export type ChatStreamFailureKind = "http" | "sse" | "missing_final" | "aborted";

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
  if (parsed.event === "agent_activity") {
    handlers.onAgentActivity?.(payload as unknown as NonNullable<ChatResponse["agent_activity"]>[number]);
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
  if (parsed.event === "requirement_update") {
    handlers.onRequirementUpdate?.(payload as unknown as RequirementUpdateStreamPayload);
    return;
  }
  if (parsed.event === "board_task_update") {
    handlers.onBoardTaskUpdate?.(payload as unknown as BoardTaskUpdateStreamPayload);
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

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function streamRequest(
  path: string,
  payload: unknown,
  handlers: ChatStreamHandlers,
  options?: { signal?: AbortSignal }
): Promise<ChatResponse> {
  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: options?.signal,
    });
  } catch (fetchError) {
    if (isAbortError(fetchError) || options?.signal?.aborted) {
      throw new ChatStreamTransportError("聊天流已停止", "aborted");
    }
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
    if (isAbortError(streamError) || options?.signal?.aborted) {
      throw new ChatStreamTransportError("聊天流已停止", "aborted");
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
  listPackageSources(packageId: string) {
    return request<SourceIngestionRecord[]>(`/api/packages/${packageId}/sources`);
  },
  deletePackageSource(packageId: string, sourceId: string) {
    return request<SourceIngestionRecord>(`/api/packages/${packageId}/sources/${sourceId}`, {
      method: "DELETE",
    });
  },
  renamePackageSource(packageId: string, sourceId: string, title: string) {
    return request<SourceIngestionRecord>(`/api/packages/${packageId}/sources/${sourceId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },
  retryPackageSource(packageId: string, sourceId: string) {
    return request<SourceIngestionRecord>(`/api/packages/${packageId}/sources/${sourceId}/retry`, {
      method: "POST",
    });
  },
  getPackageSourceContent(packageId: string, sourceId: string) {
    return request<SourceContentView>(`/api/packages/${packageId}/sources/${sourceId}/content`);
  },
  updatePackageSourceContent(packageId: string, sourceId: string, content: string) {
    return request<SourceContentView>(`/api/packages/${packageId}/sources/${sourceId}/content`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    });
  },
  async downloadPackageSource(packageId: string, sourceId: string) {
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/sources/${sourceId}/download`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "资料下载失败"));
    }
    return response.blob();
  },
  getPackageSourceStructure(packageId: string, sourceId: string) {
    return request<SourceStructureView>(`/api/packages/${packageId}/sources/${sourceId}/structure`);
  },
  rebuildPackageSourceStructure(packageId: string, sourceId: string) {
    return request<SourceStructureView>(`/api/packages/${packageId}/sources/${sourceId}/structure/rebuild`, {
      method: "POST",
    });
  },
  async importPackageSource(
    packageId: string,
    payload: { file?: File | null; sourceUri?: string; text?: string; title?: string }
  ) {
    const formData = new FormData();
    if (payload.file) {
      formData.append("file", payload.file);
    }
    if (payload.sourceUri) {
      formData.append("source_uri", payload.sourceUri);
    }
    if (payload.text) {
      formData.append("text", payload.text);
    }
    if (payload.title) {
      formData.append("title", payload.title);
    }
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/sources`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      const message = await responseErrorMessage(response, `Source import failed with ${response.status}`);
      throw new Error(message);
    }
    return response.json() as Promise<SourceIngestionRecord>;
  },
  listResearchNotes(packageId: string) {
    return request<ResearchNote[]>(`/api/packages/${packageId}/research/notes`);
  },
  createResearchNote(packageId: string, payload: ResearchNoteCreate) {
    return request<ResearchNote>(`/api/packages/${packageId}/research/notes`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateResearchNote(packageId: string, noteId: string, payload: ResearchNoteUpdate) {
    return request<ResearchNote>(`/api/packages/${packageId}/research/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteResearchNote(packageId: string, noteId: string) {
    return request<ResearchNote>(`/api/packages/${packageId}/research/notes/${noteId}`, {
      method: "DELETE",
    });
  },
  searchResearch(packageId: string, payload: ResearchSearchRequest) {
    return request<ResearchSearchResponse>(`/api/packages/${packageId}/research/search`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  askResearch(packageId: string, payload: ResearchAskRequest) {
    return request<ResearchAskResponse>(`/api/packages/${packageId}/research/ask`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listResearchThreads(packageId: string) {
    return request<ResearchChatThread[]>(`/api/packages/${packageId}/research/threads`);
  },
  createResearchThread(packageId: string, payload: ResearchChatThreadCreate) {
    return request<ResearchChatThread>(`/api/packages/${packageId}/research/threads`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateResearchThread(packageId: string, threadId: string, payload: ResearchChatThreadUpdate) {
    return request<ResearchChatThread>(`/api/packages/${packageId}/research/threads/${threadId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteResearchThread(packageId: string, threadId: string) {
    return request<ResearchChatThread>(`/api/packages/${packageId}/research/threads/${threadId}`, {
      method: "DELETE",
    });
  },
  listResearchThreadMessages(packageId: string, threadId: string) {
    return request<ResearchChatMessage[]>(`/api/packages/${packageId}/research/threads/${threadId}/messages`);
  },
  sendResearchThreadMessage(packageId: string, threadId: string, payload: ResearchChatRequest) {
    return request<ResearchChatResponse>(`/api/packages/${packageId}/research/threads/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listResearchArtifacts(packageId: string) {
    return request<ResearchArtifact[]>(`/api/packages/${packageId}/research/artifacts`);
  },
  createResearchArtifact(packageId: string, payload: ResearchArtifactCreate) {
    return request<ResearchArtifact>(`/api/packages/${packageId}/research/artifacts`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getResearchArtifact(packageId: string, artifactId: string) {
    return request<ResearchArtifact>(`/api/packages/${packageId}/research/artifacts/${artifactId}`);
  },
  retryResearchArtifact(packageId: string, artifactId: string) {
    return request<ResearchArtifact>(`/api/packages/${packageId}/research/artifacts/${artifactId}/retry`, {
      method: "POST",
    });
  },
  deleteResearchArtifact(packageId: string, artifactId: string) {
    return request<ResearchArtifact>(`/api/packages/${packageId}/research/artifacts/${artifactId}`, {
      method: "DELETE",
    });
  },
  getResearchCapabilities(packageId: string) {
    return request<ResearchCapabilities>(`/api/packages/${packageId}/research/capabilities`);
  },
  async getResearchArtifactAudio(packageId: string, artifactId: string) {
    const response = await fetch(`${getApiBase()}/api/packages/${packageId}/research/artifacts/${artifactId}/audio`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      const message = await responseErrorMessage(response, `Audio request failed with ${response.status}`);
      throw new Error(message);
    }
    return response.blob();
  },
  listResearchTransformations(packageId: string) {
    return request<ResearchTransformation[]>(`/api/packages/${packageId}/research/transformations`);
  },
  createResearchTransformation(packageId: string, payload: ResearchTransformationCreate) {
    return request<ResearchTransformation>(`/api/packages/${packageId}/research/transformations`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateResearchTransformation(packageId: string, transformationId: string, payload: ResearchTransformationUpdate) {
    return request<ResearchTransformation>(`/api/packages/${packageId}/research/transformations/${transformationId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteResearchTransformation(packageId: string, transformationId: string) {
    return request<ResearchTransformation>(`/api/packages/${packageId}/research/transformations/${transformationId}`, {
      method: "DELETE",
    });
  },
  runResearchTransformation(packageId: string, transformationId: string, payload: ResearchTransformationRun) {
    return request<ResearchArtifact>(`/api/packages/${packageId}/research/transformations/${transformationId}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  listResearchSpeakerProfiles(packageId: string) {
    return request<ResearchSpeakerProfile[]>(`/api/packages/${packageId}/research/speaker-profiles`);
  },
  createResearchSpeakerProfile(packageId: string, payload: ResearchSpeakerProfileCreate) {
    return request<ResearchSpeakerProfile>(`/api/packages/${packageId}/research/speaker-profiles`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateResearchSpeakerProfile(packageId: string, profileId: string, payload: ResearchSpeakerProfileUpdate) {
    return request<ResearchSpeakerProfile>(`/api/packages/${packageId}/research/speaker-profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteResearchSpeakerProfile(packageId: string, profileId: string) {
    return request<ResearchSpeakerProfile>(`/api/packages/${packageId}/research/speaker-profiles/${profileId}`, {
      method: "DELETE",
    });
  },
  listResearchEpisodeProfiles(packageId: string) {
    return request<ResearchEpisodeProfile[]>(`/api/packages/${packageId}/research/episode-profiles`);
  },
  createResearchEpisodeProfile(packageId: string, payload: ResearchEpisodeProfileCreate) {
    return request<ResearchEpisodeProfile>(`/api/packages/${packageId}/research/episode-profiles`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateResearchEpisodeProfile(packageId: string, profileId: string, payload: ResearchEpisodeProfileUpdate) {
    return request<ResearchEpisodeProfile>(`/api/packages/${packageId}/research/episode-profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteResearchEpisodeProfile(packageId: string, profileId: string) {
    return request<ResearchEpisodeProfile>(`/api/packages/${packageId}/research/episode-profiles/${profileId}`, {
      method: "DELETE",
    });
  },
  confirmEvidence(lessonId: string, bundleId: string, action: "confirm" | "skip") {
    return request<EvidenceConfirmationResult>(`/api/lessons/${lessonId}/evidence/confirm`, {
      method: "POST",
      body: JSON.stringify({
        bundle_id: bundleId,
        action,
      }),
    });
  },
  getPendingEvidence(lessonId: string) {
    return request<EvidenceBundle | null>(`/api/lessons/${lessonId}/evidence/pending`);
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
  async exportHtml(lessonId: string) {
    const response = await fetch(`${getApiBase()}/api/lessons/${lessonId}/document/export-html`, {
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
  streamChatOnLesson(
    lessonId: string,
    payload: ChatRequestPayload,
    handlers: ChatStreamHandlers,
    options?: { signal?: AbortSignal }
  ) {
    return streamRequest(`/api/lessons/${lessonId}/chat/stream`, payload, handlers, options);
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
  runScopeAction(
    lessonId: string,
    message: string,
    selection: ChatRequestPayload["selection"],
    scopeAction: ScopeAction
  ) {
    return api.chatOnLesson(lessonId, {
      message,
      selection,
      scope_action: scopeAction,
    });
  },
};

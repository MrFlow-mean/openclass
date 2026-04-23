import type {
  ChatRequestPayload,
  ChatResponse,
  CoursePackage,
  DocumentAIEditPayload,
  DocumentSavePayload,
  RealtimeConnectPayload,
  RealtimeConnectResponse,
  RealtimeEventLogPayload,
  ScopeAction,
} from "@/types";

const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

function getApiBase() {
  if (configuredApiBase) {
    return configuredApiBase;
  }
  if (typeof window !== "undefined" && window.location.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && !(init?.body instanceof FormData) && !(init?.body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  getCoursePackage() {
    return request<CoursePackage>("/api/course-package");
  },
  generateLesson(topic: string, branchFromLessonId?: string, startBlank = false) {
    return request<CoursePackage>("/api/lessons/generate", {
      method: "POST",
      body: JSON.stringify({
        topic,
        branch_from_lesson_id: branchFromLessonId ?? null,
        start_blank: startBlank,
      }),
    });
  },
  saveDocument(lessonId: string, payload: DocumentSavePayload) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/document/save`, {
      method: "POST",
      body: JSON.stringify(payload),
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
  connectRealtime(lessonId: string, payload: RealtimeConnectPayload) {
    return request<RealtimeConnectResponse>(`/api/lessons/${lessonId}/realtime/connect`, {
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
    return navigator.sendBeacon(`${getApiBase()}/api/lessons/${lessonId}/realtime/events`, blob);
  },
  async uploadResource(file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${getApiBase()}/api/resources/upload`, {
      method: "POST",
      body: formData,
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed with ${response.status}`);
    }
    return response.json() as Promise<CoursePackage>;
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

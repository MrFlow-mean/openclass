import type {
  ChatRequestPayload,
  ChatResponse,
  CoursePackage,
  PatchOperation,
  ScopeAction,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
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
  generateLesson(topic: string, branchFromLessonId?: string) {
    return request<CoursePackage>("/api/lessons/generate", {
      method: "POST",
      body: JSON.stringify({
        topic,
        branch_from_lesson_id: branchFromLessonId ?? null,
      }),
    });
  },
  manualCommit(lessonId: string, operations: PatchOperation[], label: string, message: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/manual-commit`, {
      method: "POST",
      body: JSON.stringify({ operations, label, message }),
    });
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
  applyProposal(lessonId: string, operations: PatchOperation[], label: string, message: string) {
    return request<CoursePackage>(`/api/lessons/${lessonId}/apply-proposal`, {
      method: "POST",
      body: JSON.stringify({ operations, label, message }),
    });
  },
  async uploadResource(file: File) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${API_BASE}/api/resources/upload`, {
      method: "POST",
      body: formData,
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

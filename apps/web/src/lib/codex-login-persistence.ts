import type { CodexLoginStartResponse } from "@/types";

const PENDING_CODEX_LOGIN_STORAGE_KEY = "openclass.codex.login.pending";

export const CODEX_LOGIN_POLL_INTERVAL_MS = 2500;

export function clearPendingCodexLogin() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(PENDING_CODEX_LOGIN_STORAGE_KEY);
  } catch {
    // Session storage may be unavailable in restricted browsing contexts.
  }
}

export function readPendingCodexLogin(): CodexLoginStartResponse | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const serialized = window.sessionStorage.getItem(PENDING_CODEX_LOGIN_STORAGE_KEY);
    if (!serialized) {
      return null;
    }
    const value = JSON.parse(serialized) as Partial<CodexLoginStartResponse>;
    if (
      typeof value.login_id !== "string" ||
      !value.login_id ||
      typeof value.user_code !== "string" ||
      !value.user_code ||
      typeof value.verification_url !== "string" ||
      !value.verification_url
    ) {
      clearPendingCodexLogin();
      return null;
    }
    return {
      login_id: value.login_id,
      user_code: value.user_code,
      verification_url: value.verification_url,
      expires_at: typeof value.expires_at === "string" ? value.expires_at : null,
    };
  } catch {
    clearPendingCodexLogin();
    return null;
  }
}

export function storePendingCodexLogin(login: CodexLoginStartResponse) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(
      PENDING_CODEX_LOGIN_STORAGE_KEY,
      JSON.stringify({
        login_id: login.login_id,
        user_code: login.user_code,
        verification_url: login.verification_url,
        expires_at: login.expires_at ?? null,
      })
    );
  } catch {
    // The active page can still poll even when session storage is unavailable.
  }
}

export function pendingCodexLoginExpired(login: CodexLoginStartResponse) {
  if (!login.expires_at) {
    return false;
  }
  const expiresAt = Date.parse(login.expires_at);
  return Number.isFinite(expiresAt) && expiresAt <= Date.now();
}

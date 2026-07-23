from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import os
from pathlib import Path
import shutil
from typing import Any, Literal
from urllib.parse import urlparse


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def is_youtube_url(raw: str) -> bool:
    try:
        hostname = (urlparse(raw.strip()).hostname or "").lower()
    except ValueError:
        return False
    return hostname in YOUTUBE_HOSTS


class MediaAccessConfigurationError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class YouTubeAccessConfig:
    js_runtime: Literal["deno", "node", "quickjs"]
    js_runtime_path: str | None
    ejs_available: bool
    browser_authorization_enabled: bool
    browser_name: str
    browser_profile: str | None
    cookie_file: str | None

    def youtube_dl_options(self, *, use_browser_authorization: bool) -> dict[str, Any]:
        runtime: dict[str, str | None] = {"path": self.js_runtime_path}
        options: dict[str, Any] = {
            "js_runtimes": {self.js_runtime: runtime},
        }
        if not use_browser_authorization:
            return options
        if not self.browser_authorization_enabled:
            raise MediaAccessConfigurationError(
                "This OpenClass deployment has not enabled trusted local YouTube authorization.",
                code="youtube_browser_authorization_disabled",
            )
        if self.cookie_file:
            options["cookiefile"] = self.cookie_file
        else:
            options["cookiesfrombrowser"] = (
                self.browser_name,
                self.browser_profile,
                None,
                None,
            )
        return options

    def require_runtime(self) -> None:
        if not self.js_runtime_path:
            raise MediaAccessConfigurationError(
                f"YouTube extraction requires the configured {self.js_runtime} JavaScript runtime.",
                code="youtube_js_runtime_unavailable",
            )
        if not self.ejs_available:
            raise MediaAccessConfigurationError(
                "YouTube extraction requires the yt-dlp-ejs package installed alongside yt-dlp.",
                code="youtube_ejs_unavailable",
            )


def youtube_access_config() -> YouTubeAccessConfig:
    runtime = (os.getenv("OPENCLASS_YOUTUBE_JS_RUNTIME") or "node").strip().lower()
    if runtime not in {"deno", "node", "quickjs"}:
        raise MediaAccessConfigurationError(
            "OPENCLASS_YOUTUBE_JS_RUNTIME must be deno, node, or quickjs.",
            code="youtube_js_runtime_invalid",
        )
    configured_path = (os.getenv("OPENCLASS_YOUTUBE_JS_RUNTIME_PATH") or "").strip()
    runtime_path = _resolve_executable(configured_path or runtime)
    cookie_file = _validated_cookie_file(
        (os.getenv("OPENCLASS_YOUTUBE_COOKIE_FILE") or "").strip()
    )
    return YouTubeAccessConfig(
        js_runtime=runtime,  # type: ignore[arg-type]
        js_runtime_path=runtime_path,
        ejs_available=_distribution_available("yt-dlp-ejs"),
        browser_authorization_enabled=_env_enabled(
            "OPENCLASS_YOUTUBE_BROWSER_AUTH_ENABLED"
        ),
        browser_name=(os.getenv("OPENCLASS_YOUTUBE_BROWSER") or "chrome").strip().lower(),
        browser_profile=(os.getenv("OPENCLASS_YOUTUBE_BROWSER_PROFILE") or "").strip() or None,
        cookie_file=cookie_file,
    )


def media_access_runtime_status() -> dict[str, object]:
    try:
        config = youtube_access_config()
    except MediaAccessConfigurationError as exc:
        return {
            "youtube_ready": False,
            "youtube_error_code": exc.code,
            "youtube_error": str(exc),
        }
    return {
        "youtube_ready": bool(config.js_runtime_path and config.ejs_available),
        "youtube_js_runtime": config.js_runtime,
        "youtube_js_runtime_path": config.js_runtime_path or "",
        "youtube_ejs_available": config.ejs_available,
        "youtube_browser_authorization_enabled": config.browser_authorization_enabled,
        "youtube_browser": config.browser_name,
        "youtube_cookie_file_configured": bool(config.cookie_file),
    }


def _resolve_executable(raw: str) -> str | None:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        return str(resolved) if resolved.is_file() and os.access(resolved, os.X_OK) else None
    discovered = shutil.which(raw)
    return str(Path(discovered).resolve()) if discovered else None


def _validated_cookie_file(raw: str) -> str | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise MediaAccessConfigurationError(
            "OPENCLASS_YOUTUBE_COOKIE_FILE must be an absolute path.",
            code="youtube_cookie_file_invalid",
        )
    if path.is_symlink() or not path.is_file():
        raise MediaAccessConfigurationError(
            "OPENCLASS_YOUTUBE_COOKIE_FILE must identify a regular non-symlink file.",
            code="youtube_cookie_file_invalid",
        )
    return str(path.resolve())


def _distribution_available(name: str) -> bool:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def _env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}

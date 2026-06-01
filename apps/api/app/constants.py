"""跨模块共享的业务常量（error code、commit kind、贡献状态）。"""

from typing import Final

from app.models import CourseContributionStatus as CourseContributionStatus

# ---------------------------------------------------------------------------
# Auth HTTP detail.code（与 auth_service._raise_auth_error / current_admin 一致）
# ---------------------------------------------------------------------------

AUTH_ERROR_UNAUTHENTICATED: Final = "unauthenticated"
AUTH_ERROR_EMAIL_NOT_VERIFIED: Final = "email_not_verified"
AUTH_ERROR_INVALID_CREDENTIALS: Final = "invalid_credentials"
AUTH_ERROR_ACCOUNT_DISABLED: Final = "account_disabled"
AUTH_ERROR_EMAIL_ALREADY_REGISTERED: Final = "email_already_registered"
AUTH_ERROR_PASSWORD_RESET_INVALID: Final = "password_reset_invalid"
AUTH_ERROR_USER_NOT_FOUND: Final = "user_not_found"
AUTH_ERROR_ADMIN_SELF_LOCKOUT: Final = "admin_self_lockout"
AUTH_ERROR_ADMIN_REQUIRED: Final = "admin_required"

# ---------------------------------------------------------------------------
# Lesson commit metadata.kind
# ---------------------------------------------------------------------------

COMMIT_KIND_CHAT_FLOW: Final = "chat_flow"
COMMIT_KIND_MANUAL_DOCUMENT_SAVE: Final = "manual_document_save"
COMMIT_KIND_BOARD_DOCUMENT_EDIT: Final = "board_document_edit"
COMMIT_KIND_BOARD_DOCUMENT_GENERATION: Final = "board_document_generation"
COMMIT_KIND_BOARD_DOCUMENT_IMPORT: Final = "board_document_import"
COMMIT_KIND_COURSE_CONTRIBUTION_MERGE: Final = "course_contribution_merge"
COMMIT_KIND_DOCUMENT_EVIDENCE_INSERT: Final = "document_evidence_insert"
COMMIT_KIND_DOCUMENT_EVIDENCE_GENERATION: Final = "document_evidence_generation"
COMMIT_KIND_DOCUMENT_EVIDENCE_LOOKUP: Final = "document_evidence_lookup"
COMMIT_KIND_INTERACTION_FLOW: Final = "interaction_flow"

# ---------------------------------------------------------------------------
# 开放课程贡献状态：类型唯一定义在 models，这里仅 re-export 字符串常量，避免漂移
# ---------------------------------------------------------------------------

CONTRIBUTION_STATUS_OPEN: Final = "open"
CONTRIBUTION_STATUS_CHANGES_REQUESTED: Final = "changes_requested"
CONTRIBUTION_STATUS_MERGED: Final = "merged"
CONTRIBUTION_STATUS_CLOSED: Final = "closed"

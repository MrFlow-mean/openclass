#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from app.models import RealtimeTranscriptLogRequest  # noqa: E402
from app.services import workspace_state  # noqa: E402
from app.services.openai_realtime import persist_realtime_transcript_event  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore learner-visible Realtime transcripts from the AI usage log into lesson history."
    )
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Persist the selected messages. Default is dry-run.")
    return parser.parse_args()


def _fallback_event_id(event: dict[str, Any], payload: dict[str, Any]) -> str:
    identity = json.dumps(
        {
            "occurred_at": event.get("occurred_at"),
            "role": payload.get("role"),
            "content": payload.get("content"),
            "metadata": payload.get("metadata"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"realtime-log-{digest}"


def _matching_events(log_file: Path, lesson_id: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    with log_file.open("r", encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            line = raw_line.strip()
            if not line:
                continue
            event = json.loads(line)
            payload = event.get("payload")
            if event.get("event_type") != "ai_interaction_message" or not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if payload.get("channel") != "realtime" or metadata.get("lesson_id") != lesson_id:
                continue
            role = payload.get("role")
            content = str(payload.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            matches.append(
                {
                    "line_number": line_number,
                    "occurred_at": event.get("occurred_at"),
                    "client_event_id": str(payload.get("message_id") or _fallback_event_id(event, payload)),
                    "client_session_id": metadata.get("client_session_id"),
                    "turn_id": metadata.get("turn_id"),
                    "lesson_title": metadata.get("lesson_title"),
                    "role": role,
                    "transport_event_type": str(payload.get("transport") or "realtime_log_backfill"),
                    "transcript": content,
                }
            )
    matches.sort(key=lambda item: (str(item["occurred_at"] or ""), int(item["line_number"])))
    return matches


def main() -> int:
    args = _arguments()
    log_file = args.log_file.expanduser().resolve()
    if not log_file.is_file():
        raise SystemExit(f"Log file does not exist: {log_file}")

    workspace = workspace_state.load_workspace_for_user(args.user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, args.lesson_id)
    existing_event_ids = {
        str(commit.metadata.get("realtime_client_event_id"))
        for commit in lesson.history_graph.commits
        if commit.metadata.get("realtime_client_event_id")
    }
    events = _matching_events(log_file, args.lesson_id)
    unique_events: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for event in events:
        event_id = str(event["client_event_id"])
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        unique_events.append(event)

    pending = [event for event in unique_events if event["client_event_id"] not in existing_event_ids]
    role_counts = {
        role: sum(1 for event in pending if event["role"] == role)
        for role in ("user", "assistant")
    }
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "lesson_id": args.lesson_id,
                    "lesson_title": lesson.title,
                    "matched": len(events),
                    "unique": len(unique_events),
                    "already_persisted": len(unique_events) - len(pending),
                    "pending": len(pending),
                    "pending_by_role": role_counts,
                },
                ensure_ascii=False,
            )
        )
        return 0

    restored = 0
    skipped = 0
    for event in pending:
        request = RealtimeTranscriptLogRequest.model_validate(event)
        if persist_realtime_transcript_event(args.lesson_id, request, user_id=args.user_id):
            restored += 1
        else:
            skipped += 1
    print(
        json.dumps(
            {
                "mode": "apply",
                "lesson_id": args.lesson_id,
                "lesson_title": lesson.title,
                "restored": restored,
                "skipped": skipped,
                "restored_by_role": role_counts,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

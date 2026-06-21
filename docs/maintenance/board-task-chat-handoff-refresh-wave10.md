# BoardTask Chat Handoff Refresh - Wave 10

Base: `e71a5c9168db37ef126ccbb8f574359f840e258f` (`origin/main` on 2026-06-21).

Branch: `codex/prep/board-task-chat-handoff-refresh-wave10-e71a`.

Scope:

- Refresh the thin BoardTask chat handoff adapter candidate from the Wave 9
  preparation branch.
- Keep production wiring out of this branch.
- Keep `InteractionSession` start ownership in the existing start path.

Prepared files:

- `apps/api/app/services/chat/paths/board_task_chat.py`
- `apps/api/tests/board_task/test_board_task_chat_handoff.py`

Boundary:

- The adapter only validates `route == "chat"`, converts the completed
  `BoardTaskRequirementSheet` into task requirements, attaches board-search and
  decision metadata, and delegates to an injected InteractionSession starter.
- The adapter does not start or clear sessions by itself.
- The adapter does not write commits, consume BoardTask history, save workspace
  state, record workflow trace nodes, or assemble `ChatResponse`.
- Future production replay should wire the current inline `decision.route ==
  "chat"` branch in `chatbot.py` to this adapter after the earlier BoardTask
  terminal extractions land.

Validation:

- `test_chat_handoff_builds_task_requirements_and_delegates_to_interaction_start`
  verifies the handoff payload and confirms the canonical start trace comes from
  the injected starter.
- `test_chat_handoff_does_not_duplicate_interaction_start_lifecycle` verifies
  the adapter itself does not emit lifecycle trace nodes or mutate
  `active_interaction_session`.
- `test_chat_handoff_none_from_interaction_start_returns_none_after_handoff_attempt`
  preserves the existing fallback behavior when the start path declines.
- `test_chat_handoff_uses_resolution_focus_when_decision_has_no_target_focus`
  preserves route-decision/focus fallback behavior.
- `test_chat_handoff_rejects_non_chat_route_without_touching_requirements`
  protects the adapter boundary.

No domain-specific keywords, textbook branches, fixed lesson content, fixed
HTML, demo content, or single-sample production logic were introduced.

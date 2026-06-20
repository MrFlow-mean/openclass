# Wave 8 Compatibility Drift Guard Handoff

base_sha: c413a192e7805df95b14b86809afe661d5721dd1
branch: codex/prep/compatibility-drift-guard-wave8-c413
head_sha: see final handoff after commit
scope: preparation-only inventory plus AST drift guard; no production behavior change

## Owned Symbols

- `teaching_action`: legacy ChatRequest control for section-by-section board teaching.
- `direct_edit`: legacy ChatInteractionMode used by the composer, selection popover, document AI edit facade, and backend compatibility edit path.
- `DOCUMENT_WRITE_ACTIONS` / `EDIT_ACTIONS` / `explain_target`: legacy append/edit/explain fallback action surface in `chatbot.py`.
- `_generate_board_directed_explanation_message`: private board explanation wrapper around `board_explanation_gate`.
- `_maybe_inherit_recent_board_edit_focus`, `_latest_successful_board_edit_focus`, `_recent_board_edit_focus_for_commit`: recent edit/write follow-up focus helpers.
- `_execute_board_task_write`, `_gate_board_directed_explanation_message`, `_process_chat_on_lesson`, `_document_ai_edit_request`: old private aliases still used by extracted handlers, service facade, or tests.

## Files

- `scripts/check_chatbot_compat_drift.py`: AST and symbol guard for pending migration functions and call sites.
- `apps/api/tests/board_task/test_chatbot_compat_drift_guard.py`: pytest wrapper for the guard.
- `docs/maintenance/compatibility-drift-guard-wave8.md`: this handoff and inventory.

## Compatibility Decisions

| Area | Decision | Evidence | Rationale |
|---|---|---|---|
| `teaching_action` | Keep for now; deprecate after parity tests. | `ChatRequest.teaching_action` exists in `apps/api/app/models.py`; frontend sends `teaching_action: "continue"` from `use-lesson-chat-agent.ts`; `decide_chat_turn` routes any teaching action to `existing_board_task`; `_handle_existing_board_task_flow` intentionally returns `None` for teaching action so the legacy branch can call `teach_first_section` / `teach_next_section`; tests cover continue and blocked directive behavior in `test_ai_logging.py`. | This is a real caller path, not dead code. It preserves `board_teaching_progress` and board-side explanation directive gating. Do not delete until section teaching is modeled as canonical board-task or interaction flow with equivalent tests. |
| `direct_edit` | Keep for now; mark as migration target. | `ChatInteractionMode = Literal["ask", "direct_edit"]`; composer and selection popover still set direct edit mode; `document_ai_edit_request` constructs a `ChatRequest(interaction_mode="direct_edit")`; `board_task_decider` maps direct edit to append/simplify/expand/rewrite; `_chat_response` still has a direct-edit fallback. | It is still a UI affordance and API compatibility mode. Migrate by routing through `BoardTaskRequirementSheet` and target resolution first, then remove only after selection/focus failure/metadata parity tests pass. |
| Legacy append/edit/explain fallback | Deprecate, but keep guarded. | `_chat_response` still handles `action_type in {*DOCUMENT_WRITE_ACTIONS, "explain_target"}`; append calls `edit_existing_document` without focus; edit resolves focus then calls `edit_existing_document`; explain resolves focus then calls `_generate_board_directed_explanation_message`. Architecture docs already classify this as `LEGACY_DOCUMENT_ACTION`. | It is the old direct action handler. Do not expand. Delete one action at a time only after canonical board-task write/edit/explain tests cover equivalent behavior and commit metadata. |
| Fallback board explanation | Deprecate, but keep guarded. | `_chat_response` has a late `_requests_explanation(request.message) and not is_document_empty(...)` branch that builds `target_excerpt` from selection/reference or board summary, then calls `_generate_board_directed_explanation_message`; docs classify it as `LEGACY_FALLBACK_EXPLAIN`. | It still gates Chatbot through `BoardExplanationDirective`, so it is safer than free answer, but it is not the preferred second-layer `BoardTaskRequirementSheet` path. Remove only after existing-board explain fallback cases route through board task collection. |
| Recent edit/write follow-up | Keep and later move into target resolution. | `_maybe_inherit_recent_board_edit_focus` reuses recent `recent_board_edit_focus` / `resolved_focus` metadata for generic edit/write follow-ups; `_recent_board_edit_focus_for_commit` records the focus after successful board-task writes/edits; tests cover direct follow-up edit/write inheritance. | This is a generic target-location hint, not a subject-specific rule. Later home should be `BOARD_TARGET_RESOLVE`, not top-level orchestration. |
| Old private aliases | Keep as compatibility imports; do not add new uses. | `chatbot.py` imports `handle_board_task_write_terminal as _execute_board_task_write` and `generate_board_directed_explanation_message as _gate_board_directed_explanation_message`; `chat_service.py` imports public facade calls as private aliases; handler tests still wire several `chatbot_module._...` helpers. | These aliases keep extraction prep branches and focused handler tests small. Replace with public dependency builders as handlers graduate; do not delete before tests stop importing them. |

## Guard Behavior

`scripts/check_chatbot_compat_drift.py` parses Python files with `ast` and fails if pending migration contracts drift accidentally:

- `models.py` must still expose `ChatInteractionMode` values `ask/direct_edit`, `TeachingAction` values `continue/restart`, and `ChatRequest` fields for compatibility controls.
- `chat_turn_gate.py` must still dispatch `teaching_action` and recognize `direct_edit` as an existing-board task signal.
- `board_task_decider.py` must keep the direct-edit branch and its append/simplify/expand/rewrite mappings.
- `workflow_trace.py` must keep legacy compatibility `NodeId` values so docs and traces do not silently lose migration vocabulary.
- `chatbot.py` must keep the pending migration functions, private aliases, board-task bypasses, legacy document action branch, fallback board explanation branch, recent-follow-up helpers, and expected call sites.
- Frontend caller symbols are checked textually so the guard catches removal of real `teaching_action` / `direct_edit` callers before backend cleanup.
- This doc is checked for the base SHA and required handoff sections.

Intentional migration rule: if a later PR genuinely removes one of these compatibility paths, that PR must update this guard in the same diff and cite the replacement tests that prove parity.

## Tests

Run:

```bash
python3 scripts/check_chatbot_compat_drift.py
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_chatbot_compat_drift_guard.py
```

The guard is read-only and does not import the app runtime. It only parses source files and checks caller symbols.

## Risks

- The guard is deliberately conservative. It will fail on legitimate migration PRs until the replacement tests and guard updates land together.
- It does not prove behavior parity by itself; it only prevents silent removal or reshaping of known compatibility surfaces.
- It checks frontend caller symbols textually because the repo has no TypeScript AST utility in the Python test environment.
- Existing docs line numbers in `chat-workflow-graph.md` may drift as parallel branches extract handlers; this guard avoids line-number assertions for that reason.

## Expected Conflicts

- Low textual conflict risk: this branch adds new files only.
- Medium semantic conflict risk with branches that edit `chatbot.py`, `chat_turn_gate.py`, `board_task_decider.py`, `models.py`, or frontend chat caller files.
- High intentional-failure likelihood after branches that complete removal of `teaching_action`, `direct_edit`, legacy document actions, fallback board explanation, or private aliases. Those branches should update this guard as part of their migration proof.

## Recommended Integration Order

1. Land canonical board-task write/edit/explain trace and replay work first if those branches are already in review.
2. Merge this prep guard before deleting or rewriting compatibility paths, so cleanup PRs must explicitly update the migration contract.
3. For each cleanup PR, remove one compatibility area at a time: first add parity tests, then update runtime, then update this guard and this inventory.
4. Migrate recent edit/write follow-up into target resolution before deleting the orchestration helpers.
5. Remove old private aliases only after extracted handlers depend on public dependency builders or their own module-local helpers.

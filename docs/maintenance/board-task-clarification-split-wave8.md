# BoardTask Clarification Split Wave 8 Handoff

## Scope

- `base_sha`: `c413a192e7805df95b14b86809afe661d5721dd1`
- Specification source: prep PR `#96` only. Do not merge or cherry-pick the old worker branch.
- Commit A ownership: standalone confirmation-decline terminal handler plus direct tests.
- Commit A non-goals: no production `chatbot.py` wiring; no write/edit/explain/chat execution changes; no route precedence, API, SSE, schema, prompt, or database changes.

## Candidate Split

| Candidate | Proposed handler module | Proposed test ownership | Call-site replacement | Durable order | Trace order | Active BoardTask/history behavior |
| --- | --- | --- | --- | --- | --- | --- |
| A. confirmation decline | `apps/api/app/services/chat/paths/board_task_confirmation_decline.py` | `apps/api/tests/board_task/test_board_task_confirmation_decline_handler.py`; keep existing workflow coverage in `test_workflow_trace.py` after wiring | Replace the awaiting write confirmation decline branch inside `_handle_existing_board_task_flow` | `not_executed` operation -> commit -> normalize -> save histories -> terminal trace -> response | `BOARD_TASK_COLLECT(awaiting_confirmation)` -> `BOARD_WRITE_CONFIRMATION_HANDLE(declined)` -> `RESPONSE_ASSEMBLE` | Clears `lesson.board_task_requirements`; run becomes `not_executed`; active task is absent from response; commit metadata has `board_task_route=await_write_confirmation`, `board_task_cleared=True` |
| B. missing fields | `apps/api/app/services/chat/paths/board_task_missing_fields.py` | Direct handler tests for message generation, metadata, save failure; workflow trace tests remain the integration guard | Replace `if board_task.progress < 100` terminal branch | existing `record_update` already happened in orchestrator -> `BOARD_TASK_COLLECT` -> message -> commit -> normalize -> save -> terminal trace -> response | `BOARD_TASK_COLLECT(collecting)` -> `BOARD_TASK_CLARIFY_FIELDS(missing_fields)` -> `RESPONSE_ASSEMBLE` | Keeps active task collecting; preserves `missing_items` and `clarification_question`; no clearing; history run remains `collecting` |
| C. normal location clarification | `apps/api/app/services/chat/paths/board_task_location_clarification.py` | Direct handler tests for ambiguous and missing location; workflow trace integration after wiring | Replace only the non-edit-conversion body of `decision.route == "clarify_location"` | route decision exists -> `BOARD_TASK_COLLECT` -> `BOARD_TARGET_RESOLVE` -> update active task -> emit update -> message -> commit -> normalize -> save -> terminal trace -> response | `BOARD_TASK_COLLECT(ready)` -> `BOARD_TARGET_RESOLVE(ambiguous/missing)` -> `BOARD_ROUTE_CLARIFY_LOCATION(...)` -> `RESPONSE_ASSEMBLE` | Active task remains ready; `location_status` becomes `ambiguous` or `missing`; edit `failure_count` increment must happen before branching to D |
| D. unresolved edit to write conversion | `apps/api/app/services/chat/paths/board_task_unresolved_edit_conversion.py` | Direct handler tests for old run archival and new write task creation; keep old workflow tests as integration | Replace the nested edit `failure_count >= 2` branch, called before C continues | record old failed edit update -> mark old run `not_executed` -> create new write task -> record new `awaiting_confirmation` version -> emit update -> message -> commit -> normalize -> save -> terminal trace -> response | inherited `BOARD_TASK_COLLECT` and `BOARD_TARGET_RESOLVE` from caller -> `BOARD_AWAIT_WRITE_CONFIRMATION(converted_from_unresolved_edit)` -> `RESPONSE_ASSEMBLE` | Old edit task is cleared/not executed in metadata; new active task is write/awaiting confirmation; commit metadata must include old `board_task_*` plus `new_board_task`, `new_board_task_run_id`, `new_board_task_version_id` |
| E. await write confirmation prompt | `apps/api/app/services/chat/paths/board_task_await_write_confirmation.py` | Direct handler tests for content_absent prompt and save failure; workflow trace integration after wiring | Replace `decision.route == "await_write_confirmation"` branch | route decision exists -> `BOARD_TASK_COLLECT` -> transform task to write/content_absent/awaiting -> record awaiting version -> emit update -> message -> commit -> normalize -> save -> terminal trace -> response | `BOARD_TASK_COLLECT(ready)` -> `BOARD_AWAIT_WRITE_CONFIRMATION(awaiting_confirmation)` -> `RESPONSE_ASSEMBLE` | Active task remains present with `requested_action=write`, `location_status=content_absent`, `confirmation_status=awaiting`, no missing fields |

## Behavior Invariants

- Keep `chatbot.py` as orchestrator only; Commit A has no production call-site change.
- Do not change write/edit/explain/chat execution handlers.
- Do not add natural-language rules or regexes.
- Do not introduce subject, textbook, exam, or demo hardcoding.
- Terminal trace must be recorded only after the relevant commit/history save succeeds.
- `RESPONSE_ASSEMBLE` must remain last and only after response construction succeeds.
- Save-failure tests should assert that terminal trace and response trace are absent.

## Metadata Contract

- A: existing task metadata only, `route="await_write_confirmation"`, `cleared=True`.
- B: collecting task metadata, `route="clarify_location"`, `cleared=False`.
- C: updated active task metadata, `route=decision.route`, `decision=decision.model_dump(mode="json")`, `cleared=False`, plus board search evidence.
- D: old task metadata with `cleared=True`, plus `new_board_task`, `new_board_task_run_id`, and `new_board_task_version_id`.
- E: updated awaiting write task metadata, `route="await_write_confirmation"`, `decision=decision.model_dump(mode="json")`, `cleared=False`, plus board search evidence.

## Recommended Split Order

1. Land candidate A as a standalone handler and direct tests.
2. Wire candidate A in a separate Commit B and run existing workflow trace coverage.
3. Extract B as standalone handler and wire separately.
4. Extract E next because it has no nested old-run/new-run handoff.
5. Extract C and D together only if C delegates D through a clearly named conversion handler; otherwise extract D first, then C.
6. After all terminal handlers are wired, do one narrow cleanup commit to remove dead inline code from `chatbot.py`.

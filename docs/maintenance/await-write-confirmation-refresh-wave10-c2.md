# Await Write Confirmation Refresh Wave 10 C2

## Scope

- Branch: `codex/prep/await-write-confirmation-refresh-wave10-e71a`
- Base: `origin/main` at `e71a5c9168db37ef126ccbb8f574359f840e258f`
- Goal: refresh the await-write-confirmation handler candidate only.
- Production wiring: not changed.

## Candidate

Added `apps/api/app/services/chat/paths/board_task_await_write_confirmation.py`.

The candidate extracts the current content-absent board-task terminal behavior into a standalone handler shape:

- records the ready board task collection trace before persistence;
- normalizes the active task into `requested_action=write`, `location_status=content_absent`, and `confirmation_status=awaiting`;
- records an `awaiting_confirmation` board task version and emits the sheet update;
- commits the confirmation prompt metadata with `board_task_route=await_write_confirmation`;
- saves workspace/history state before recording the durable await trace;
- returns a response with the active board task still awaiting confirmation.

## Tests

Added `apps/api/tests/board_task/test_board_task_await_write_confirmation_handler.py`.

The focused tests cover:

- successful await-write-confirmation persistence, metadata, response, emitted sheet, and workflow trace order;
- save failure behavior before durable await trace is recorded;
- response failure behavior after the await state has been saved;
- route guard rejection for non-`await_write_confirmation` route decisions.

## Integration Notes

- This prep does not modify `chatbot.py`; the live branch in `_handle_existing_board_task_flow` remains the production path.
- A later integrator can replace the inline `decision.route == "await_write_confirmation"` branch with this handler and pass the existing helper dependencies.
- The candidate intentionally mirrors the current metadata labels and trace node names to keep replay review small.
- No schema, router, shared helper, or model changes are required by this candidate.

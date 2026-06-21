# Normal Location Clarification Refresh - Wave 10 C4

Base verified: `e71a5c9168db37ef126ccbb8f574359f840e258f`

Branch: `codex/prep/normal-location-clarification-refresh-wave10-e71a`

Scope:

- Added a preparation-only `handle_board_task_location_clarification` candidate.
- Kept the candidate limited to `route="clarify_location"` with `location_status` of `missing` or `ambiguous`.
- Preserved the unresolved edit conversion boundary by rejecting edit tasks whose next failed lookup should convert into write confirmation.
- Did not wire the handler into `chatbot.py`; current production clarify-location behavior remains unchanged.

Genericity check:

- The handler is driven by generic `BoardTaskRequirementSheet`, `BoardTaskRouteDecision`, `FocusResolution`, and history metadata.
- It does not inspect subject names, resource names, lesson titles, textbooks, exams, or demo text.
- It does not generate board content or bypass BoardTask history, target resolution, commit metadata, or response assembly.

Files:

- `apps/api/app/services/chat/paths/board_task_location_clarification.py`
- `apps/api/tests/board_task/test_board_task_location_clarification_handler.py`

Validation:

- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_location_clarification_handler.py`
  - Result: 4 passed.
- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_workflow_trace.py::test_existing_board_clarify_location_trace_records_response_after_success`
  - Result: 1 passed.

Handoff notes:

- This branch is prep-only and should not be opened as a production PR by itself.
- Production wiring should wait until unresolved edit conversion owns its terminal path, so normal location clarification does not compete with that conversion branch for `clarify_location`.
- A later integrator can replace the existing inline normal clarify-location block with this handler after constructing dependencies from the existing `chatbot.py` helpers.

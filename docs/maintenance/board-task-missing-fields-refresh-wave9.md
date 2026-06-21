# Board Task Missing Fields Refresh Wave 9 Prep

Base: `555e4ca8214c84f878d5488be01ebd4969db6aa7`

Branch: `codex/prep/board-task-missing-fields-refresh-wave9-555e`

Evidence commit read with `git show`: `0531a22531c4e307fc78585f7a515b691f04f663`

## Scope

- Added a standalone existing-board task missing-fields terminal handler proposal.
- Added direct handler tests for successful persistence, save-failure trace boundaries, and ready-task guard behavior.
- Did not integrate the handler into `chatbot.py`.
- Did not touch router, schema, workflow trace, model, or shared test helper files.

## Contract

- The active `BoardTaskRequirementSheet` stays active while the run is `collecting`.
- `missing_items` and `clarification_question` pass through unchanged.
- The durable board-task run and version remain `collecting` after the terminal response succeeds.
- `BOARD_TASK_CLARIFY_FIELDS` is recorded only after workspace save succeeds.
- `RESPONSE_ASSEMBLE` is recorded only after response construction succeeds.
- A save failure records no missing-fields terminal trace and no `RESPONSE_ASSEMBLE`.
- A ready board task is rejected before commit, save, or response construction.

## Handoff

This prep branch intentionally leaves production routing untouched. A later integration branch can wire the handler only after deciding whether the existing inline missing-fields block in `chatbot.py` should be replaced as-is or adapted with additional decision metadata.

## Verification

```bash
.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_missing_fields_handler.py -q
```

# Board Task Missing Fields Wave 8 Prep

Base: `c2bef6be6a6da387025116a3ff8f8ec740b12b15`

Branch: `codex/prep/board-task-missing-fields-wave8-c2be`

## Scope

- Added a standalone existing-board task missing-fields terminal handler.
- Added direct handler tests for successful persistence and save-failure trace boundaries.
- Did not integrate the handler into `chatbot.py`.
- Did not add shared abstractions or central-file changes.

## Contract

- The active `BoardTaskRequirementSheet` stays active while the run is `collecting`.
- `missing_items` and `clarification_question` are passed through unchanged.
- The durable board-task run and version remain `collecting` after the terminal response succeeds.
- `BOARD_TASK_CLARIFY_FIELDS` is recorded only after workspace save succeeds.
- `RESPONSE_ASSEMBLE` is recorded only after response construction succeeds.
- A save failure records no missing-fields terminal trace and no `RESPONSE_ASSEMBLE`.

## Verification

```bash
.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_missing_fields_handler.py -q
```

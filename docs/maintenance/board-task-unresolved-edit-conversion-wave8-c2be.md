# Board Task Unresolved Edit Conversion - Wave 8 Prep

## Scope

- Base SHA: `c2bef6be6a6da387025116a3ff8f8ec740b12b15`
- Branch: `codex/prep/board-task-unresolved-edit-conversion-wave8-c2be`
- Runtime wiring: not changed
- Central files: `apps/api/app/services/chatbot.py` not modified

## Prepared Files

- `apps/api/app/services/chat/paths/board_task_unresolved_edit_conversion.py`
- `apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py`

## Contract

The standalone handler converts only the generic second failed `edit` location
miss on a `clarify_location` route:

1. Record the old edit run as `not_executed`.
2. Create a new write task in `awaiting_confirmation`.
3. Commit chat metadata with old `board_task_run_id` /
   `board_task_version_id` and new `new_board_task_run_id` /
   `new_board_task_version_id`.
4. Keep the new write task active through `lesson.board_task_requirements`,
   `new_board_task`, and `active_board_task_sheet_after`.
5. Return `None` for normal location clarification paths so the existing
   clarification handler remains the owner.

## Verification

```bash
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py -q
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_write_handler.py -q
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m py_compile apps/api/app/services/chat/paths/board_task_unresolved_edit_conversion.py apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py
```

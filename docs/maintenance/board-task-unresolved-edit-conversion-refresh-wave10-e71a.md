# Board Task Unresolved Edit Conversion - Wave 10 C3 Refresh

## Scope

- Base SHA: `e71a5c9168db37ef126ccbb8f574359f840e258f`
- Branch: `codex/prep/unresolved-edit-conversion-refresh-wave10-e71a`
- Refreshed from: `codex/prep/board-task-unresolved-edit-conversion-wave8-c2be`
- Runtime wiring: not changed
- Central files: `apps/api/app/services/chatbot.py` not modified

## Prepared Files

- `apps/api/app/services/chat/paths/board_task_unresolved_edit_conversion.py`
- `apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py`
- `docs/maintenance/board-task-unresolved-edit-conversion-refresh-wave10-e71a.md`

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

## Wave 10 Notes

- This is a prep-only candidate. It does not import or call the handler from
  `chatbot.py`.
- The old unresolved edit run is archived as `not_executed` before the new
  write confirmation run becomes active.
- The conversion uses only generic BoardTask fields: `requested_action`,
  `location_status`, `failure_count`, and `question_or_topic` / `target_hint`.
- No subject, textbook, exam, or demo-specific branches were added.
- Production replay should wire this before the normal missing / ambiguous
  location clarification handler, preserving the existing unresolved edit
  boundary.

## Verification

```bash
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py -q
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_board_task_write_handler.py -q
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m py_compile apps/api/app/services/chat/paths/board_task_unresolved_edit_conversion.py apps/api/tests/board_task/test_board_task_unresolved_edit_conversion_handler.py
```

# Board Task Confirmation Decline Wave 8 Handoff

## Scope

- `base_sha`: `c2bef6be6a6da387025116a3ff8f8ec740b12b15`
- `branch`: `codex/prep/board-task-confirmation-decline-wave8-c2be`
- Evidence source only: `codex/prep/board-task-clarification-split-wave8-c413` at `1a45301a557a5e86e206388618f1027d5a8f86a8`
- Production wiring is intentionally not included. Do not commit `apps/api/app/services/chatbot.py` in this prep branch.

## Prepared Files

- `apps/api/app/services/chat/paths/board_task_confirmation_decline.py`
- `apps/api/tests/board_task/test_board_task_confirmation_decline_handler.py`

## Call-Site Replacement Notes

Replace only the `is_write_decline(request.message)` branch inside `_handle_existing_board_task_flow` after the existing guard:

```python
if (
    existing_task is not None
    and existing_task.confirmation_status == "awaiting"
    and existing_task.requested_action == "write"
):
    if is_write_decline(request.message):
        return handle_board_task_confirmation_decline(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            existing_task=existing_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            source_interaction_metadata=interaction_metadata,
            deps=_board_task_confirmation_decline_deps(),
        )
```

The future wiring commit should add imports for `BoardTaskConfirmationDeclineDependencies` and `handle_board_task_confirmation_decline`, plus a local `_board_task_confirmation_decline_deps()` factory that passes:

- `_board_task_metadata`
- `commit_operations`
- `workspace_state.normalize_package_state`
- `_save_workspace_for_user`
- `_response`

## Verification Expectations

- Decline response has no chatbot message.
- `lesson.board_task_requirements` is cleared.
- Active board task is absent from the response.
- Board task run persists as `not_executed`.
- Commit metadata includes `assistant_message_source="board_task_cancelled"`.
- Commit metadata includes `board_task_route="await_write_confirmation"` and `board_task_cleared=True`.
- Save failure records no `BOARD_WRITE_CONFIRMATION_HANDLE` terminal trace and no `RESPONSE_ASSEMBLE` trace.

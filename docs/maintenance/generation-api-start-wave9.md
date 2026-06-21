# Explicit API Start Generation Lane - Wave 9 Prep

Base SHA: `738b378aff760d27e38a6130254f2a6a0e73b99b`

Branch: `codex/prep/generation-api-start-wave9-738b`

This is a prep-only worker branch. Do not merge it directly. The branch proposes
a standalone handler and focused tests for the explicit
`board_generation_action="start"` API lane without changing central
orchestration.

## Scope

Own only the explicit API start lane:

- `request.board_generation_action == "start"`
- blank-board initial generation that already reached the existing central
  start branch
- frozen learning requirement checkpoint before BoardEditor
- retryable `generation_failed` handling
- board-generation commit metadata for `board_generation_action="start"`
- trace contract proposal for the currently untraced central branch

Out of scope:

- confirmed-resource generation
- text-based generation-control requests without `board_generation_action`
- `knowledge_board` minimal requirement generation
- existing-board generation task execution
- API, SSE, schema, prompt, or `NodeId` changes
- common generation engine or shared runtime abstraction
- `chatbot.py` production edits on this branch

## Trigger / Precedence Contract

The trigger is exact:

```python
request.board_generation_action == "start"
```

This branch must not capture:

- `is_generation_control_request(message)`
- `is_explicit_board_generation_request(message)`
- `resource_reference_action == "confirm"`
- `InitialLearningWorkModeDecision.work_mode == "knowledge_board"`

The current central order must be preserved:

1. Load context and decide the board action.
2. Run `decide_chat_turn(...)`.
3. Resolve resource preflight with direct reference disabled for explicit start.
4. Give an active interaction session first chance to handle the turn.
5. Give existing-board task flow first chance when the gate selected it.
6. Give initial learning work mode first chance on blank-board learning turns.
7. Only then handle explicit `board_generation_action == "start"`.

For existing boards, `decide_chat_turn(...)` maps explicit start to the
existing-board task route. The future integration should keep the proposed
handler at the current central call site, not move it above existing-board task
handling.

## Freeze Checkpoint Contract

The handler proposal preserves the current durable sequence:

1. Read latest learning clarification for the lesson.
2. Stamp task details as `action_type="generate_board"` with the user message.
3. Prepare the initial requirement for board generation.
4. Persist and emit the frozen requirement checkpoint before BoardEditor.
5. Call BoardEditor only with the frozen `requirement_run_id` and
   `frozen_requirement_version_id`.

If the requirement was not already ready, `_prepare_initial_requirement_for_board_generation(...)`
normalizes the clarification to forced-start and records a `forced_frozen`
version. This is not a shortcut around history; it is the audited forced-start
path.

## Failure Retryability Contract

If BoardEditor returns `changed=False`:

- append a `generation_failed` event when a frozen run exists
- keep the learning requirement run status `frozen`
- do not write a board-generation commit
- preserve the current board document
- save workspace state with the failure event
- return the failure operation status and failure reason

The frozen run remains retryable because `generation_failed` is an event, not a
terminal run status.

## Metadata Contract

The successful commit metadata must preserve the current explicit-start shape:

```text
kind = "board_document_generation"
user_message = request.message
assistant_message = post-generation chatbot message
assistant_message_source = post-generation source
board_editor_message = edit_outcome.chatbot_message
board_generation_action = "start"
board_edit_operation = edit_outcome.operation
board_edit_summary = edit_outcome.summary
board_section_titles = edit_outcome.section_titles
board document quality metadata
requirement history metadata with run_status_after_commit="consumed"
task metadata with requirement_cleared=True
```

Do not add resource provenance metadata here. Confirmed-resource generation owns
that contract separately.

## Trace Gap

Current `main` has a trace gap in the explicit API start branch:

- the branch freezes before BoardEditor and records retryable failure history
- the branch does not record the initial generation trace nodes around that work
- existing coverage in `test_ready_requirement_generation_trace.py` currently
  asserts that explicit start keeps the old trace scope

The proposed handler records only existing nodes:

Success:

```text
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_BOARD_COMMIT
RESPONSE_ASSEMBLE
```

Failure:

```text
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_GENERATION_FAILED
RESPONSE_ASSEMBLE
```

`RESPONSE_ASSEMBLE` is recorded only after response construction succeeds.

## Standalone Handler Proposal

Added:

- `apps/api/app/services/chat/paths/generation_api_start.py`

The handler mirrors the current central branch with dependency injection, like
the existing ready-requirement handler pattern, but it intentionally does not
reuse or introduce a common engine.

The proposal is not wired into `chatbot.py` on this branch.

## Focused Tests

Added:

- `apps/api/tests/board_task/test_generation_api_start_handler.py`

Coverage:

- success forced-freezes before generation, commits, consumes, saves, and then
  assembles the response
- generation failure leaves the run frozen and retryable without a commit
- response build failure does not record `RESPONSE_ASSEMBLE`
- non-`start` request is rejected before dependencies run

## Exact Central Call-Site Replacement

Future integration should replace only the current block:

```text
apps/api/app/services/chatbot.py:3970-4097
```

Replace the body of:

```python
if request.board_generation_action == "start":
    ...
```

with:

```python
return handle_generation_api_start(
    workspace=workspace,
    package=package,
    lesson=lesson,
    user_id=user_id,
    request=request,
    requirements=requirements,
    resource_summary=_resource_summary(visible_package.resources),
    selected_reference=selected_reference,
    requirement_history=requirement_history,
    track_initial_requirement_run=track_initial_requirement_run,
    deps=GenerationApiStartDependencies(
        latest_learning_clarification=_latest_learning_clarification,
        with_task_details=_with_task_details,
        prepare_initial_requirement_for_board_generation=(
            _prepare_initial_requirement_for_board_generation
        ),
        checkpoint_initial_requirement_before_generation=(
            _checkpoint_initial_requirement_before_generation
        ),
        generate_from_requirements=generate_from_requirements,
        refresh_lesson_runtime=refresh_lesson_runtime,
        build_board_teaching_guide=build_board_teaching_guide,
        post_initial_board_generation_message=_post_initial_board_generation_message,
        commit_operations=commit_operations,
        clear_task_requirements=_clear_task_requirements,
        board_document_failure_metadata=_board_document_failure_metadata,
        board_document_quality_metadata=_board_document_quality_metadata,
        requirement_history_metadata=_requirement_history_metadata,
        task_metadata=_task_metadata,
        save_workspace_for_user=_save_workspace_for_user,
        build_response=_response,
    ),
)
```

Required import in the future integration PR:

```python
from app.services.chat.paths.generation_api_start import (
    GenerationApiStartDependencies,
    handle_generation_api_start,
)
```

Do not move this call earlier in `_chat_response(...)`, and do not combine it
with ready-requirement generation, confirmed-resource generation, or
knowledge-board generation.

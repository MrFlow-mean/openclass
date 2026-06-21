# Text-Triggered Generation Control - Wave 9 Prep

Base SHA: `738b378aff760d27e38a6130254f2a6a0e73b99b`

Branch: `codex/prep/generation-control-wave9-738b`

Worker: P10

## Scope

Own only blank-board generation reached from learner text:

- text `generation_control_request`: `is_generation_control_request(...)` plus an
  existing actionable requirement context.
- text `document_artifact_request`:
  `turn_intent.wants_document_artifact_generation(...)`.
- the direct BoardEditor generation path after resource prompt and
  confirmed-resource precedence have already been applied.

Out of scope:

- `board_generation_action="start"` API start.
- `resource_reference_action="confirm"` confirmed-resource generation.
- `knowledge_board` minimal first-board generation from
  `InitialLearningWorkModeDecision`.
- existing-board second-layer BoardTask routing.
- new API, SSE, schema, prompt, common engine, shared runtime container, or new
  `NodeId` values.

## Trigger And Precedence Contract

The production call order should stay:

1. Existing-board generation control remains a BoardTask path.
2. `_handle_initial_learning_work_mode(...)` keeps first chance to consume
   `unknown`, `narrow_topic`, and `knowledge_board`.
3. `board_generation_action="start"` keeps the existing API-start block.
4. The text-triggered branch updates `LearningRequirementSheet` from the latest
   learner text.
5. If `resource_resolution.reference_prompt` exists and no
   `resource_reference_action` is present, keep using
   `handle_generation_resource_prompt(...)`.
6. If `resource_reference_action == "confirm"` and `selected_reference` exists,
   keep using `_generate_board_from_confirmed_resource(...)`.
7. Only then classify and execute the text-triggered generation-control /
   document-artifact request.

The prep handler makes that final step explicit through
`classify_text_triggered_generation_request(...)`. It returns `None` for API
start, confirmed-resource turns, resource-prompt turns, and nonblank documents.

## Freeze Checkpoint Contract

Before BoardEditor is called, the handler must:

- apply `_with_task_details(..., action_type="generate_board")`;
- call `_prepare_initial_requirement_for_board_generation(...)`;
- call `_checkpoint_initial_requirement_before_generation(...)`;
- persist the frozen run/version in the requirement history store;
- pass `requirement_run_id` and `frozen_requirement_version_id` into
  `generate_from_requirements(...)`.

If the learner forced generation from a partially complete requirement, the
frozen version remains `forced_frozen`; this is a normal audited path, not a
state-machine bypass.

## Failure Retryability Contract

If BoardEditor returns `changed=False`:

- append a durable `generation_failed` event;
- keep the active requirement run `frozen`;
- keep `consumed_commit_id` empty;
- do not write a board-generation commit;
- save the workspace and requirement history;
- return `board_document_operation_status="failed"` and preserve the active
  requirement sheet so the same frozen version can be retried.

## Metadata Contract

Success commit metadata keeps the existing board-generation surface and adds the
lane discriminator:

```text
kind = "board_document_generation"
board_generation_action = "explicit_board_request"
generation_request_lane = "text_triggered_generation"
generation_request_trigger = "generation_control_request" | "document_artifact_request"
requirement_run_id = frozen run id
frozen_requirement_version_id = frozen version id
requirement_run_status_after_commit = "consumed"
task_requirement_sheet = frozen sheet
learning_clarification = frozen clarification
resource_resolution_status = current ResourceResolution status
```

The handler also preserves existing editor metadata:
`board_editor_message`, `board_edit_operation`, `board_edit_summary`,
`board_section_titles`, and board document quality metadata.

## Trace Gap

Current `main` has durable side effects in the text-triggered direct generation
block but does not trace them:

- `apps/api/app/services/chatbot.py:4695` freezes/checkpoints the requirement.
- `apps/api/app/services/chatbot.py:4711` calls BoardEditor.
- `apps/api/app/services/chatbot.py:4720` handles generation failure without
  `INITIAL_GENERATION_FAILED`.
- `apps/api/app/services/chatbot.py:4761` commits success without
  `INITIAL_BOARD_COMMIT`.
- The block returns responses without `RESPONSE_ASSEMBLE`.

The proposed handler records existing nodes only:

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

No trace key is persisted into response JSON, SSE payloads, requirement history
rows, or commit metadata.

## Standalone Handler Proposal

Added proposal module:

```text
apps/api/app/services/chat/paths/text_triggered_generation.py
```

Public proposal entry points:

- `classify_text_triggered_generation_request(...)`
- `handle_text_triggered_generation_request(...)`
- `TextTriggeredGenerationDependencies`

The handler is deliberately narrow and duplicates only the dependency protocol
shape needed for this path. It does not introduce a common generation engine.

## Focused Tests

Added focused tests:

```text
apps/api/tests/board_task/test_text_triggered_generation_handler_proposal.py
```

Covered contracts:

- classifier owns only blank-board text-triggered generation after resource
  prompt precedence;
- API start, confirmed-resource, resource-prompt, and existing-board turns are
  excluded;
- success freezes before generate, commits, consumes, saves, responds, and
  records trace;
- failure persists a retryable frozen run without a board-generation commit.

## Exact Central Call-Site Replacement

Do not touch `chatbot.py` in this prep branch. For the production replay, add
the import near the existing chat path imports:

```python
from app.services.chat.paths.text_triggered_generation import (
    TextTriggeredGenerationDependencies,
    classify_text_triggered_generation_request,
    handle_text_triggered_generation_request,
)
```

Then replace only `apps/api/app/services/chatbot.py:4684-4819` with:

```python
        generation_request = classify_text_triggered_generation_request(
            lesson=lesson,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
        )
        if generation_request is not None:
            return handle_text_triggered_generation_request(
                generation_request=generation_request,
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_summary_for_turn=resource_summary_for_turn,
                resource_resolution=resource_resolution,
                selected_reference=selected_reference,
                requirement_history=requirement_history,
                track_initial_requirement_run=track_initial_requirement_run,
                deps=TextTriggeredGenerationDependencies(
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
                    reference_metadata=_reference_metadata,
                    save_workspace_for_user=_save_workspace_for_user,
                    build_response=_response,
                ),
            )
```

Keep the surrounding `handle_generation_resource_prompt(...)` block at
`chatbot.py:4650-4669`, the confirmed-resource block at `chatbot.py:4670-4683`,
and the fallback chat handoff starting at `chatbot.py:4820`.

## Handoff JSON

```json
{
  "worker": "P10",
  "branch": "codex/prep/generation-control-wave9-738b",
  "base_sha": "738b378aff760d27e38a6130254f2a6a0e73b99b",
  "scope": "text-triggered generation-control/document-artifact request lane only",
  "touched_chatbot_py": false,
  "api_start_in_scope": false,
  "confirmed_resource_in_scope": false,
  "knowledge_board_in_scope": false,
  "common_engine_added": false,
  "handler_proposal": "apps/api/app/services/chat/paths/text_triggered_generation.py",
  "focused_tests": "apps/api/tests/board_task/test_text_triggered_generation_handler_proposal.py",
  "central_call_site_replacement": "apps/api/app/services/chatbot.py:4684-4819",
  "trace_nodes_added_by_proposal": [],
  "trace_nodes_reused": [
    "INITIAL_REQUIREMENT_FREEZE",
    "INITIAL_BOARD_GENERATE",
    "INITIAL_BOARD_COMMIT",
    "INITIAL_GENERATION_FAILED",
    "RESPONSE_ASSEMBLE"
  ],
  "ready_for_production_replay": true
}
```

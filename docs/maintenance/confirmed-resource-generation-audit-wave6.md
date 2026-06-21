# Confirmed Resource Generation Audit - Wave 6

Branch: `codex/prep/confirmed-resource-generation-audit-wave6`

Base audited: `origin/main` at `80e246ade787e0342a39d3ce603f1208663e5ab0`

PR #95 read-only comparison: `origin/pr/95` at `210eb4ded1b5c2c3b6202da90a06ed3df7bdb6a3`

## Scope

This prep audit covers only confirmed-resource first-board generation:

- `apps/api/app/services/chatbot.py::_generate_board_from_confirmed_resource(...)`
- its two confirmed-resource callers in `chatbot.py`
- related resource confirmation, requirement history, and workflow trace tests

No production code was changed. This proposal does not require API, SSE, schema, prompt, or commit metadata changes.

## Current Durable Order

The confirmed-resource flow already has the durable state order needed for safe generation:

1. Ready requirement before resource prompt:
   - `handle_generation_resource_prompt(...)` persists the ready learning requirement before asking for resource confirmation.
   - It records `INITIAL_REQUIREMENT_COLLECT`, then `RESOURCE_REFERENCE_PROMPT`, then persists a chat commit and assembles the response.
   - Existing coverage: `test_generation_resource_prompt_trace_records_requirement_collect_before_prompt`.
2. Confirm action:
   - The main generation/document-artifact branch calls `_generate_board_from_confirmed_resource(...)` when `request.resource_reference_action == "confirm"` and a `selected_reference` exists.
   - The later confirmation guard repeats the same helper call when `_should_generate_board_after_reference_confirmation(...)` holds.
3. Freeze/checkpoint before BoardEditor:
   - `_generate_board_from_confirmed_resource(...)` normalizes the action to `generate_board`.
   - `_prepare_initial_requirement_for_board_generation(...)` reuses an existing frozen snapshot or freezes the current ready requirement.
   - Current direct tests observe the frozen version can have `change_kind="forced_frozen"` after the confirmation turn, while the durable run status is still `frozen`; the trace contract should key off the frozen boundary rather than expose this internal change-kind detail.
   - `_checkpoint_initial_requirement_before_generation(...)` saves the frozen requirement history and emits the requirement update before calling `generate_from_requirements(...)`.
4. BoardEditor call:
   - `generate_from_requirements(...)` receives `reference_context=resource_resolution.selected_reference`.
   - It also receives `requirement_run_id` and `frozen_requirement_version_id` when tracking is enabled.
5. Failure:
   - If `edit_outcome.changed` is false, `requirement_history.generation_failed(...)` is appended.
   - The workspace is saved and the response returns the failed operation status.
   - No board-generation commit is written, and the requirement run remains frozen/retryable.
6. Success:
   - The board runtime is refreshed and a post-generation learner-facing message is generated.
   - A `board_document_generation` commit is written with `resource_backed_generation=True` and `board_generation_action="resource_reference_confirm"`.
   - The frozen requirement is consumed with the commit id.
   - Active learning requirements are cleared, the workspace is saved, and the response returns the consume stamp.

## PR #95 Comparison

PR #95 adds workflow trace boundaries to the stable ready-generation contract for the non-resource path:

- `INITIAL_REQUIREMENT_READY`
- `INITIAL_REQUIREMENT_FREEZE`
- `INITIAL_BOARD_GENERATE`
- `INITIAL_GENERATION_FAILED`
- `INITIAL_BOARD_COMMIT`
- `RESPONSE_ASSEMBLE`

The PR deliberately leaves confirmed-resource generation out of that production trace expansion. Its new test suite includes a confirmed-resource scope guard asserting that those ready-generation trace nodes remain absent for confirmed-resource generation.

This means the next production implementation should not cherry-pick PR #95 mechanically. It should add the same durable trace vocabulary to `_generate_board_from_confirmed_resource(...)` after confirmation, while removing or updating the PR #95 confirmed-resource scope guard.

## Proposed Contract

For confirmed-resource generation, the production trace contract should become:

Success path:

```text
CONTEXT_LOAD
TURN_CONTEXT_BUILD
BOARD_ACTION_DECIDE
CHAT_TURN_GATE
RESOURCE_PREFLIGHT
ACTIVE_INTERACTION_CHECK
INITIAL_REQUIREMENT_READY
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_BOARD_COMMIT
RESPONSE_ASSEMBLE
```

Failure path:

```text
CONTEXT_LOAD
TURN_CONTEXT_BUILD
BOARD_ACTION_DECIDE
CHAT_TURN_GATE
RESOURCE_PREFLIGHT
ACTIVE_INTERACTION_CHECK
INITIAL_REQUIREMENT_READY
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_GENERATION_FAILED
RESPONSE_ASSEMBLE
```

Durability expectations:

- The ready requirement must already exist before the resource confirmation prompt.
- The frozen requirement must be saved before `generate_from_requirements(...)`.
- `generate_from_requirements(...)` must receive the frozen run/version ids and the selected resource reference context.
- Failure must append `generation_failed`, keep the run frozen, avoid a success commit, and return the operation failure status.
- Success must commit, consume with the commit id, clear the active requirement sheet, save, and respond.
- Trace ids must stay internal; no trace keys should be added to API responses or commit metadata.

## Prep Test Proposal

`apps/api/tests/board_task/test_confirmed_resource_generation_contract.py` adds strict xfail contract tests for the future production trace expansion:

- success should emit the PR #95 ready-generation trace boundaries after resource confirmation
- failure should emit the same boundaries with `INITIAL_GENERATION_FAILED`, keep the run frozen, and avoid a success commit

The tests are intentionally marked `xfail(strict=True, raises=AssertionError)` because current `main` has the durable history order but does not yet emit the PR #95 trace nodes for this confirmed-resource path.

## Non-Changes Confirmed

This prep branch proposes no changes to:

- public API response shape
- SSE payload shape
- Pydantic schemas
- prompts or model routing
- commit metadata keys
- `chatbot.py` production wiring

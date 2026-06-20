# Initial Generation Trace Wave 5 Contract

This is Worker I preparation evidence for the initial board generation trace.
It is not a production implementation branch and must be replayed by the
Coordinator before acceptance.

## Scope

Base:

- `origin/main` / `MAIN_SHA`: `d658e679a71d6dad893e8909f8ba25080523f1bf`
- Branch: `codex/prep/initial-generation-trace-wave5`

Owned contract:

- Ready initial requirement state.
- Frozen requirement snapshot persisted before BoardEditor execution.
- BoardEditor generation invocation.
- Successful board commit followed by requirement run consume.
- Generation failure event that leaves the frozen run retryable.

Out of scope:

- Extraction from `chatbot.py`.
- Production changes to `chatbot.py`, `workflow_trace.py`, `models.py`,
  `routers/chat.py`, `chat_service.py`, or shared test helpers.
- Resource-confirmed generation.
- Knowledge-board minimal generation.
- Existing-board BoardTask write, edit, explain, or chat routes.
- API, SSE, `ChatResponse`, prompt, or commit metadata shape changes.

## Success Contract

For the ready blank-board generation path, the trace should be:

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

Required step metadata:

- `INITIAL_REQUIREMENT_READY`: `decision="ready"`, with the ready requirement
  `run_id` and `version_id`.
- `INITIAL_REQUIREMENT_FREEZE`: `decision="frozen"`, with the frozen
  `run_id` and `version_id`.
- `INITIAL_BOARD_GENERATE`: `decision="board_editor"`, with the frozen
  `run_id` and `version_id`. This node is recorded immediately before calling
  `generate_from_requirements(...)`.
- `INITIAL_BOARD_COMMIT`: `decision="committed"`, with `commit_id`, `run_id`,
  and `version_id`. This node is recorded only after the board commit exists
  and `LearningRequirementHistoryRecorder.consume(...)` has succeeded.

Required durable state:

- Requirement versions are `completed -> frozen`.
- Requirement events are `created -> completed -> frozen -> consumed`.
- The run status is `consumed`.
- `consumed_commit_id` equals the lesson commit id.
- BoardEditor receives `requirement_run_id` and
  `frozen_requirement_version_id` from the frozen requirement stamp.
- The committed metadata remains the existing board-generation metadata. Trace
  data must not leak into the visible response or commit metadata.

## Failure Contract

For a BoardEditor no-change / failed outcome after freeze, the trace should be:

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

Required step metadata:

- `INITIAL_GENERATION_FAILED`: `decision="generation_failed"`, with the failure
  reason plus the frozen `run_id` and `version_id`.
- `INITIAL_BOARD_COMMIT` must not be recorded.

Required durable state:

- Requirement versions are `completed -> frozen`.
- Requirement events are `created -> completed -> frozen -> generation_failed`.
- The run status remains `frozen`.
- `frozen_version_id` is retained.
- `consumed_commit_id` remains null.
- The board document remains unchanged.
- The frozen run remains retryable by the existing requirement history
  semantics.

## Prepared Tests

Prepared focused test file:

- `apps/api/tests/board_task/test_initial_generation_trace_contract.py`

The tests are intentionally marked `xfail(strict=True)` because this worker
branch does not own the production wiring. They should be un-xfailed only when
the Coordinator wires the trace nodes in the owned orchestration files.

Contract tests:

- `test_initial_ready_generation_trace_contract_records_freeze_generate_commit_consume`
- `test_initial_generation_failure_trace_contract_keeps_frozen_run_retryable`

## Coordinator Replay Notes

Likely production replay points in `apps/api/app/services/chatbot.py`:

- Around the ready requirement stamp after
  `_maybe_record_initial_requirement_update(...)`.
- Immediately after `_prepare_initial_requirement_for_board_generation(...)`.
- Immediately before `generate_from_requirements(...)`.
- After `requirement_history.generation_failed(...)` and successful save on the
  failure path.
- After `commit_operations(...)` and
  `requirement_history.consume(...)` on the success path.

The wiring should use existing `NodeId` values. No new `NodeId`, response
field, SSE event, prompt, or domain-specific branch is required.

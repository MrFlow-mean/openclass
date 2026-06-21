# Confirmed Resource Trace Activation - Wave 8

Base SHA: `c2bef6be6a6da387025116a3ff8f8ec740b12b15`

Branch: `codex/integrate/confirmed-resource-trace-activation-wave8`

Evidence source: prep PR #98 / `origin/pr/98` and
`codex/prep/confirmed-resource-trace-activation-wave8-c413`, used only for
audit evidence and contract shape. Do not merge those old branches.

## Scope

Own only confirmed-resource first-board generation after a learner confirms a
resource reference:

- `_generate_board_from_confirmed_resource(...)`
- selected resource reference provenance passed into BoardEditor
- ready requirement already persisted before the resource prompt
- frozen requirement checkpoint before BoardEditor
- durable `generation_failed` event and retryable frozen run on failure
- success commit, consume, save, and response trace

Out of scope:

- regular ready requirement generation
- explicit `board_generation_action="start"`
- `knowledge_board` generation
- handler extraction or new path wiring
- API, SSE, schema, prompt, metadata, or `NodeId` changes

## Expected Trace

Success:

```text
CONTEXT_LOAD
TURN_CONTEXT_BUILD
BOARD_ACTION_DECIDE
CHAT_TURN_GATE
RESOURCE_PREFLIGHT
ACTIVE_INTERACTION_CHECK
RESOURCE_CONFIRMED_GENERATE
INITIAL_REQUIREMENT_READY
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_BOARD_COMMIT
RESPONSE_ASSEMBLE
```

Failure:

```text
CONTEXT_LOAD
TURN_CONTEXT_BUILD
BOARD_ACTION_DECIDE
CHAT_TURN_GATE
RESOURCE_PREFLIGHT
ACTIVE_INTERACTION_CHECK
RESOURCE_CONFIRMED_GENERATE
INITIAL_REQUIREMENT_READY
INITIAL_REQUIREMENT_FREEZE
INITIAL_BOARD_GENERATE
INITIAL_GENERATION_FAILED
RESPONSE_ASSEMBLE
```

## State Invariants

- The prompt turn persists a ready/completed learning requirement before asking
  for resource confirmation.
- The confirmation turn freezes or reuses the frozen requirement before calling
  BoardEditor.
- BoardEditor receives `reference_context`, `requirement_run_id`, and
  `frozen_requirement_version_id`.
- Failure appends `generation_failed`, keeps the run frozen and retryable, saves
  workspace state, does not write a board-generation commit, and returns failure
  operation status.
- Success writes a `board_document_generation` commit with
  `resource_backed_generation=True`,
  `board_generation_action="resource_reference_confirm"`, and selected
  reference provenance; then consumes the run with the commit id, clears the
  active requirement sheet, saves, and responds.
- Workflow trace keys remain internal and must not leak into response JSON,
  SSE payloads, requirement history rows, or commit metadata.

## Commit Plan / Status

Commit A stayed characterization-only:

- Add focused contract coverage in
  `apps/api/tests/board_task/test_confirmed_resource_generation_contract.py`.
- Keep the trace activation tests strict `xfail` until the candidate patch.

Commit B is the candidate activation patch:

- Add trace records inside `_generate_board_from_confirmed_resource(...)`.
- Convert the strict `xfail` tests into passing tests.
- Update the older ready-generation confirmed-resource scope guard so it no
  longer expects those trace nodes to be absent.

No handler extraction is included in this branch.

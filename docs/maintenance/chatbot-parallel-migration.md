# Chatbot Parallel Migration Registry

This document records the parallel preparation wave for the `chatbot.py`
strangler migration and the current production integration plan. It is a
maintainer-facing coordination record, not an executable workflow.

## Current Base

- Integration base branch: `origin/main`
- Integration base SHA: `00d037ed5dd30c366ff9f96344cfc4006851acf2`
- Parallel-wave base SHA: `cb004788511748a01dd8e76604425616b8f012f6`
- PR #79: merged into `main` as `b7d504769bc69b731ef08b2f48fc59472d72477e`
- PR #83 / Lane A: merged into `main` as
  `ae938ffb012fead1b23ce38e678a39aa80293956`
- PR #82 / Lane C: merged into `main` as
  `00d037ed5dd30c366ff9f96344cfc4006851acf2`
- PR #77: still open, draft, and non-mergeable; keep it as historical evidence
  only, not as an implementation source.

Most preparation branches below were created from the old parallel-wave base.
They remain specs, tests, and audit evidence. Production integration still
requires manual replay onto fresh branches unless the lane is already marked
merged.

## Central Ownership

Only the Coordinator / Integrator may apply final accepted changes to central
orchestration files:

- `apps/api/app/services/chatbot.py`
- `apps/api/app/services/workflow_trace.py`
- `apps/api/app/models.py`
- `apps/api/app/routers/chat.py`
- `apps/api/app/services/chat_service.py`
- shared test helper modules

Worker branches are preparation-only. Do not merge them directly.

## Safety Rules

- Preserve API shape, SSE event names, `ChatResponse` fields, prompt text,
  route precedence, commit metadata, requirement history, BoardTask history,
  freeze / consume ordering, and document mutation behavior.
- Do not introduce workflow frameworks, persistent workflow trace, shared
  runtime containers, new `NodeId` values, or broad abstractions without a
  separate design PR.
- Do not add subject, textbook, exam, demo, or sample-specific branches.
- One production PR may cover only one path or one homogeneous terminal group.
- At most two production PRs should be active at once, and they must not modify
  the same `chatbot.py` function cluster.

## Lane Registry

| Lane | Branch | Parallel head SHA | Owned symbols / path | Status | Dependencies | Drift |
|---|---|---|---|---|---|---|
| A: sequence continue extraction | `codex/parallel/sequence-continue-extraction` | `398813c9cb9f08185a4dab1a15d04e51ef8cf1d7` | `_handle_section_explanation_sequence_turn`: `follow_up_current`, `advance` | production merged via #83 as `ae938ffb012fead1b23ce38e678a39aa80293956` | complete; did not touch `exit_requested` / `completed` | replayed and merged |
| B: sequence start trace | `codex/parallel/sequence-start-trace` | `1e7e65f2e0117a49a44c13e5cc1440cbebb9ab0e` | `_start_section_explanation_sequence(...)` | next production trace | must land before H | stale until replayed on `00d037e` |
| C: initial guidance trace | `codex/parallel/initial-guidance-trace` | `3fddbc2455a06f5469898a0513a7730c2e53faaf` | initial mode `unknown`, `narrow_topic` | production merged via #82 as `00d037ed5dd30c366ff9f96344cfc4006851acf2` | complete; trace-only with failure-order coverage | replayed and merged |
| D: requirement chat trace | `codex/parallel/requirement-chat-trace` | `ccc0ad15a37caa54ad51e18601ac7bef061b5828` | requirement updated but not ready terminal | repair reviewed PASS; repaired head `b83b2af007af099d234d54886e5dcfd4d7aedad9` | replay manually; do not merge repair branch | stale until replayed on `00d037e` |
| E: BoardTask clarification trace | `codex/parallel/board-task-clarification-trace` | `c3b2b459f7b91b0d32e443867f41af7d77bfa7ba` | missing fields, `clarify_location`, `await_write_confirmation`, confirmation decline | repair reviewed PASS; repaired head `c27140539979784c0a14b5cfc041af61df51433e` | wait for a production PR slot | stale until replayed on `00d037e` |
| F: BoardTask write current-main audit | `codex/parallel/board-task-write-current-main-audit` | `2a71da5771b49f2ee5ce188f281a0f981b05245e` | current-main write audit against #77 | audit complete; historical evidence only | add current-main write trace before extraction; do not reuse #77 directly | stale until replayed on `00d037e` |
| G: BoardTask edit trace | `codex/parallel/board-task-edit-trace` | `c82966b099be354a3e371ae0d61774344e7986cd` | edit success, target miss, `execution_failed`, repeated miss conversion, BoardPatch metadata, consume ordering | repair reviewed PASS; repaired head `65323b94a4b880170dc86ebad6780053bf51a8eb` | wait for a production PR slot | stale until replayed on `00d037e` |
| H: BoardTask explain trace | `codex/parallel/board-task-explain-trace` | `eb6d8cf01b81289775a0e9f1d00b78b80f3e9fe2` | single-target explanation, directive failure, sequence plan boundary, commit metadata, consume ordering | integration-blocked with B | rebase after B; remove duplicate `RESPONSE_ASSEMBLE`; expect `BOARD_SEQUENCE_START` | stale until B lands and H is replayed |

## Review Findings

- D repair moved `REQUIREMENT_CHAT_UPDATE` after durable requirement
  history/workspace save and passed state/history review at
  `b83b2af007af099d234d54886e5dcfd4d7aedad9`.
- E repair moved `RESPONSE_ASSEMBLE` after `_response(...)` succeeds and
  passed state/history review at `c27140539979784c0a14b5cfc041af61df51433e`.
- G repair moved `BOARD_TASK_READY_PERSIST`, `BOARD_TASK_FAILURE`,
  `PERSIST_BOARD_COMMIT`, and edit `RESPONSE_ASSEMBLE` trace points after their
  matching durable side effects and passed state/history review at
  `65323b94a4b880170dc86ebad6780053bf51a8eb`.
- P1: `sequence-start-trace` and `board-task-explain-trace` conflict on the
  sequence-request explain route. If both land unchanged, the route can
  double-record `RESPONSE_ASSEMBLE`, and H's test contract for
  `BOARD_SEQUENCE_START` is stale.
- Text conflicts are expected among D/E/G/H in `chatbot.py` and
  `apps/api/tests/board_task/test_workflow_trace.py`. Integrate by replaying
  one lane at a time, not by merging worker branches.
- No reviewer found domain hardcoding, new `NodeId` values, API/SSE/schema/prompt
  changes, or central-file scope creep outside candidate `chatbot.py` trace
  instrumentation.

## Integration Queue

1. Lane B production PR: trace `_start_section_explanation_sequence(...)`.
2. Lane D production PR: replay the reviewed requirement-chat terminal repair.
3. Do not open E while both Wave 2 production slots are occupied.
4. G is eligible for a later production PR slot after its final review PASS.
5. Land B before H.
6. Replay H after B lands, update its trace contract, and rerun behavior plus
   state/history review.
7. Keep F / #77 as historical audit evidence only until current-main write
   trace work starts.
8. Continue with initial generation, confirmed-resource generation,
   compatibility cleanup, and shared dependency cleanup only after the earlier
   path contracts are stable.

## Production PR Scope

### Lane B: Sequence Start Trace

Fresh branch: `codex/integrate/sequence-start-trace`

Own only:

- `_start_section_explanation_sequence(...)`

Do not touch:

- sequence continuation
- sequence exit / completion
- BoardTask explain route
- generic `InteractionSession` handling
- prompt text, API, SSE, or schema

Expected trace scope:

- `BOARD_SEQUENCE_START`
- BoardTask consume metadata
- commit/save ordering
- `RESPONSE_ASSEMBLE` after response construction succeeds

### Lane D: Requirement Chat Trace

Fresh branch: `codex/integrate/requirement-chat-trace`

Own only:

- requirement updated but not ready terminal

Replay the reviewed behavior from `b83b2af007af099d234d54886e5dcfd4d7aedad9`
manually. Do not merge the repair branch.

Expected trace scope:

- persist requirement run/version/event
- save workspace/history durably
- `REQUIREMENT_CHAT_UPDATE`
- current chat commit at the existing boundary
- `RESPONSE_ASSEMBLE` after response construction succeeds

## Repair Queue

Repair-only branches should not be merged directly:

- D: reviewed PASS; replay manually as Wave 2 production PR.
- E: reviewed PASS; wait for a production PR slot.
- G: reviewed PASS; wait for a production PR slot.
- H: blocked until B merges; then replay and accept `BOARD_SEQUENCE_START` in
  the explain-route contract.

## Notes

- The old preparation branches are useful as specs, tests, and candidate
  patches, but they are not merge branches.
- The next production work should start from `00d037e`, not from the old
  parallel-wave base.

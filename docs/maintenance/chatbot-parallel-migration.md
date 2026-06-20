# Chatbot Parallel Migration Registry

This document records the parallel preparation wave for the `chatbot.py`
strangler migration and the current production integration plan. It is a
maintainer-facing coordination record, not an executable workflow.

## Current Base

- Integration base branch: `origin/main`
- Integration base SHA: `6b6480d20ad8f15a2a068a347eba786748bbca3a`
- Latest verified `MAIN_SHA`: `6b6480d20ad8f15a2a068a347eba786748bbca3a`
- Parallel-wave base SHA: `cb004788511748a01dd8e76604425616b8f012f6`
- PR #86 / Lane H: merged into `main` as
  `73c0af289df3a49de1b4d7c6cb98d347f852bdbb`.
- PR #87 / Initial guidance extraction: merged into `main` as
  `413e8d0963b32bd29e09c7a114c5f49641fb7738`.
- PR #88 / Lane E: merged into `main` as
  `0cc1493e0ab532678b2026bb4e1115e6cd86ea3e`.
- PR #89 / Requirement chat terminal extraction: merged into `main` as
  `6b6480d20ad8f15a2a068a347eba786748bbca3a`.
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

## Wave 4 Checkpoint

Wave 4 started from `MAIN_SHA`
`413e8d0963b32bd29e09c7a114c5f49641fb7738` and closed at
`6b6480d20ad8f15a2a068a347eba786748bbca3a`.

Merged production status:

- #86 `refactor: trace board task explanation paths`: merged as
  `73c0af289df3a49de1b4d7c6cb98d347f852bdbb`; main push Verify succeeded.
- #87 `refactor: extract initial learning guidance paths`: merged as
  `413e8d0963b32bd29e09c7a114c5f49641fb7738`; main push Verify succeeded.
- #88 `refactor: trace board task clarification paths`: merged as
  `0cc1493e0ab532678b2026bb4e1115e6cd86ea3e`; main push Verify succeeded.
- #89 `refactor: extract requirement chat terminal`: merged as
  `6b6480d20ad8f15a2a068a347eba786748bbca3a`; main push Verify succeeded.

Next production queue:

- BoardTask lane: G, BoardTask edit trace.
- Initial-learning lane: initial generation trace, using I's audit contract as
  preparation evidence before production replay.

Remaining preparation-only lanes:

- F: BoardTask write trace.
- S: sequence start extraction.
- I: initial generation audit until its trace contract is replayed into a
  production branch.
- K: compatibility cleanup inventory.

Wave 4 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: trace board task clarification paths` and
  `refactor: extract requirement chat terminal`.
- Do not open G/F/S/I/K production PRs yet.
- Do not merge old prep branches directly; replay manually from latest `main`.

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
| B: sequence start trace | `codex/parallel/sequence-start-trace` | `1e7e65f2e0117a49a44c13e5cc1440cbebb9ab0e` | `_start_section_explanation_sequence(...)` | production merged as `9c1d8267b875f8ac46b6bdf7e5d26e4993d55674` | complete | replayed and merged |
| C: initial guidance trace | `codex/parallel/initial-guidance-trace` | `3fddbc2455a06f5469898a0513a7730c2e53faaf` | initial mode `unknown`, `narrow_topic` | production merged via #82 as `00d037ed5dd30c366ff9f96344cfc4006851acf2` | complete; trace-only with failure-order coverage | replayed and merged |
| D: requirement chat trace | `codex/parallel/requirement-chat-trace` | `ccc0ad15a37caa54ad51e18601ac7bef061b5828` | requirement updated but not ready terminal | trace merged via #85; terminal extraction merged via #89 as `6b6480d20ad8f15a2a068a347eba786748bbca3a` | complete | replayed and merged |
| E: BoardTask clarification trace | `codex/parallel/board-task-clarification-trace` | `c3b2b459f7b91b0d32e443867f41af7d77bfa7ba` | missing fields, `clarify_location`, `await_write_confirmation`, confirmation decline | production merged via #88 as `0cc1493e0ab532678b2026bb4e1115e6cd86ea3e` | complete; do not add more cases to `test_workflow_trace.py` | replayed and merged |
| F: BoardTask write current-main audit | `codex/parallel/board-task-write-current-main-audit` | `2a71da5771b49f2ee5ce188f281a0f981b05245e` | current-main write audit against #77 | audit complete; historical evidence only | add current-main write trace before extraction; do not reuse #77 directly | stale until replayed on `00d037e` |
| G: BoardTask edit trace | `codex/parallel/board-task-edit-trace` | `c82966b099be354a3e371ae0d61774344e7986cd` | edit success, target miss, `execution_failed`, repeated miss conversion, BoardPatch metadata, consume ordering | next BoardTask production candidate | replay manually from latest `main`; use a focused test file instead of adding to `test_workflow_trace.py` | stale until replayed on `6b6480d` |
| H: BoardTask explain trace | `codex/parallel/board-task-explain-trace` | `eb6d8cf01b81289775a0e9f1d00b78b80f3e9fe2` | single-target explanation, directive failure, sequence plan boundary, commit metadata, consume ordering | production merged via #86 as `73c0af289df3a49de1b4d7c6cb98d347f852bdbb` | complete | replayed and merged |

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
- The old `sequence-start-trace` / `board-task-explain-trace` ordering conflict
  was resolved by the #86 replay; `BOARD_SEQUENCE_START` is now an expected
  existing node.
- Future text conflicts are expected mainly around G/F in `chatbot.py`.
  Integrate by replaying one lane at a time, not by merging worker branches.
  For tests, add focused files instead of growing `test_workflow_trace.py`.
- No reviewer found domain hardcoding, new `NodeId` values, API/SSE/schema/prompt
  changes, or central-file scope creep outside candidate `chatbot.py` trace
  instrumentation.

## Integration Queue

1. BoardTask lane: G edit trace is the next production candidate.
2. BoardTask lane after G: F write current-main trace, using #77 only as
   historical evidence.
3. After BoardTask trace coverage is stable, begin BoardTask extraction PRs one
   path at a time.
4. Initial-learning lane: initial generation trace is next; use I's audit
   contract as preparation evidence, then replay manually into a production
   branch.
5. After initial generation trace lands, continue with initial generation
   extraction and confirmed-resource generation.
6. S remains standalone sequence-start extraction preparation until explicitly
   promoted; do not wire it through `chatbot.py` yet.
7. K remains docs-only compatibility cleanup inventory.
8. Do not start shared runtime/dependency cleanup yet.

## Next Production PR Scope

### Lane G: BoardTask Edit Trace

Fresh branch should start from `6b6480d20ad8f15a2a068a347eba786748bbca3a`.

Own only:

- edit success
- target miss
- `execution_failed`
- repeated miss conversion
- BoardPatch metadata
- commit/save/consume/response ordering

Testing note:

- Do not add more cases to `apps/api/tests/board_task/test_workflow_trace.py`.
  Use a focused BoardTask edit trace test file.

### Initial Generation Trace

Fresh branch should start from `6b6480d20ad8f15a2a068a347eba786748bbca3a`.

Own only the ready-to-freeze-to-BoardEditor generation path:

- ready requirement state
- frozen snapshot
- BoardEditor generation
- commit
- consume
- failure boundary

## Repair Queue

Repair-only branches should not be merged directly:

- G: reviewed PASS; next BoardTask production candidate.
- F: current-main write trace/audit evidence only until production replay.
- S: standalone extraction preparation only.
- I: initial generation audit evidence only until production trace replay.
- K: docs-only compatibility cleanup inventory.

## Notes

- The old preparation branches are useful as specs, tests, and candidate
  patches, but they are not merge branches.
- The next production work should start from
  `6b6480d20ad8f15a2a068a347eba786748bbca3a`, not from the old parallel-wave
  base.

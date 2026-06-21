# Chatbot Parallel Migration Registry

This document records the parallel preparation wave for the `chatbot.py`
strangler migration and the current production integration plan. It is a
maintainer-facing coordination record, not an executable workflow.

## Current Base

- Integration base branch: `origin/main`
- Integration base SHA: `738b378aff760d27e38a6130254f2a6a0e73b99b`
- Latest verified `MAIN_SHA`: `738b378aff760d27e38a6130254f2a6a0e73b99b`
- Parallel-wave base SHA: `cb004788511748a01dd8e76604425616b8f012f6`
- Wave 8 Slot B / confirmed-resource generation trace activation: merged into
  `main` as `738b378aff760d27e38a6130254f2a6a0e73b99b`.
- Wave 8 Slot A / BoardTask single-target explain extraction: merged into
  `main` as `79c7839648703931028bdbf617ba97944315e7b9`.
- PR #100 / BoardTask write extraction: merged into `main` as
  `9bae84d92a219f35b81d53a4cd3121c96306ff9b`.
- PR #101 / Ready requirement generation extraction: rebased after #100 and
  merged into `main` as `c413a192e7805df95b14b86809afe661d5721dd1`.
- PR #94 / Lane F: merged into `main` as
  `b9361743e18e43fd7e9326cd0505dce4d9cb8442`.
- PR #95 / Lane I: merged into `main` as
  `6f9918e880d8cd451916cafe859726f667a1bef9`.
- PR #86 / Lane H: merged into `main` as
  `73c0af289df3a49de1b4d7c6cb98d347f852bdbb`.
- PR #87 / Initial guidance extraction: merged into `main` as
  `413e8d0963b32bd29e09c7a114c5f49641fb7738`.
- PR #88 / Lane E: merged into `main` as
  `0cc1493e0ab532678b2026bb4e1115e6cd86ea3e`.
- PR #89 / Requirement chat terminal extraction: merged into `main` as
  `6b6480d20ad8f15a2a068a347eba786748bbca3a`.
- PR #90 / Wave 4 checkpoint docs: merged into `main` as
  `d658e679a71d6dad893e8909f8ba25080523f1bf`.
- PR #92 / Sequence start extraction: merged into `main` as
  `ae82bf55075ba18cb6e9c27f38889f2051b37bea`.
- PR #91 / Lane G: merged into `main` as
  `100ff1e00e314e66c998e5258c476ebcd2654286`.
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
`413e8d0963b32bd29e09c7a114c5f49641fb7738`, closed production at
`6b6480d20ad8f15a2a068a347eba786748bbca3a`, and closed checkpoint docs at
`d658e679a71d6dad893e8909f8ba25080523f1bf`.

Merged production status:

- #86 `refactor: trace board task explanation paths`: merged as
  `73c0af289df3a49de1b4d7c6cb98d347f852bdbb`; main push Verify succeeded.
- #87 `refactor: extract initial learning guidance paths`: merged as
  `413e8d0963b32bd29e09c7a114c5f49641fb7738`; main push Verify succeeded.
- #88 `refactor: trace board task clarification paths`: merged as
  `0cc1493e0ab532678b2026bb4e1115e6cd86ea3e`; main push Verify succeeded.
- #89 `refactor: extract requirement chat terminal`: merged as
  `6b6480d20ad8f15a2a068a347eba786748bbca3a`; main push Verify succeeded.
- #90 `docs: update chatbot migration Wave 4 checkpoint`: merged as
  `d658e679a71d6dad893e8909f8ba25080523f1bf`; main push Verify succeeded.

Wave 4 handoff queue:

- BoardTask lane: G, BoardTask edit trace.
- Independent extraction lane: S, sequence start extraction.
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

## Wave 5 Checkpoint

Wave 5 started from `MAIN_SHA`
`d658e679a71d6dad893e8909f8ba25080523f1bf` and closed at
`100ff1e00e314e66c998e5258c476ebcd2654286`.

Merged production status:

- #92 `refactor: extract sequence session start path`: merged as
  `ae82bf55075ba18cb6e9c27f38889f2051b37bea`; main push Verify #108
  succeeded. This completes the main sequence migration.
- #91 `refactor: trace board task edit execution paths`: amended to preserve
  current-main edit commit metadata behavior, then merged as
  `100ff1e00e314e66c998e5258c476ebcd2654286`; main push Verify #110
  succeeded.

Next production queue:

- BoardTask lane: F, BoardTask write trace.
- Generation lane: I, initial generation trace.
- Confirmed-resource lane: J waits behind I's initial generation contract.

Remaining preparation-only lanes:

- K: compatibility cleanup inventory.

Wave 5 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: extract sequence session start path` and
  `refactor: trace board task edit execution paths`.
- Do not open F/I/J/K production PRs until their lanes are promoted.
- Do not merge old prep branches directly; replay manually from latest `main`.

## Wave 6 Checkpoint

Wave 6 started from `MAIN_SHA`
`100ff1e00e314e66c998e5258c476ebcd2654286` and closed production at
`6f9918e880d8cd451916cafe859726f667a1bef9`.

Merged production status:

- #94 `refactor: trace board task write execution paths`: merged as
  `b9361743e18e43fd7e9326cd0505dce4d9cb8442`; main push Verify #27869563211
  succeeded.
- #95 `refactor: trace ready requirement board generation path`: rebased after
  #94, merged as `6f9918e880d8cd451916cafe859726f667a1bef9`; main push Verify
  #27869626745 succeeded.

Coordinator verification before #95 merge:

- Focused generation trace set: 63 passed.
- Full backend pytest: 593 passed.
- `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.

Wave 6 handoff queue:

- BoardTask extraction lane: #96 is prep-only evidence for clarification
  terminal extraction. It must be split before production replay.
- BoardTask extraction lane: #97 is prep-only evidence for single-target
  explain terminal extraction and is the preferred first BoardTask extraction
  template after write extraction.
- Confirmed-resource lane: #98 is prep-only audit evidence. Its xfailed trace
  contract must be activated in a later production PR before extraction.
- Historical PR #77 is superseded by #94 trace plus the future Wave 7 write
  extraction; do not merge it.
- Historical product/UI drafts #1, #6, and #53 are not chatbot migration
  dependencies. They need explicit product decisions before any future merge.

Next production queue:

- Wave 7 slot A: BoardTask write extraction from fresh
  `6f9918e880d8cd451916cafe859726f667a1bef9`.
- Wave 7 slot B: ready requirement generation extraction from fresh
  `6f9918e880d8cd451916cafe859726f667a1bef9`.
- Only after those land: BoardTask explain extraction, confirmed-resource trace
  activation, then confirmed-resource extraction.

Wave 6 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: trace board task write execution paths` and
  `refactor: trace ready requirement board generation path`.
- Keep #96, #97, and #98 draft and prep-only. They are replay evidence, not
  production merge branches.
- Continue using focused test files for new path contracts; do not grow
  `apps/api/tests/board_task/test_workflow_trace.py` unless the path is truly
  cross-cutting.

## Wave 7 Checkpoint

Wave 7 started from `MAIN_SHA`
`6f9918e880d8cd451916cafe859726f667a1bef9` and closed production at
`c413a192e7805df95b14b86809afe661d5721dd1`.

Merged production status:

- #100 `refactor: extract board task write terminal`: merged as
  `9bae84d92a219f35b81d53a4cd3121c96306ff9b`; main push Verify #125
  succeeded.
- #101 `refactor: extract ready requirement generation terminal`: rebased
  after #100, merged as `c413a192e7805df95b14b86809afe661d5721dd1`; main push
  Verify #127 succeeded.

Coordinator verification before #101 merge:

- Compile gate for `chatbot.py`, `ready_requirement_generation.py`, and focused
  ready generation tests succeeded.
- Focused ready generation set: 19 passed.
- BoardTask suite: 348 passed.
- AI logging regression suite: 127 passed.
- Full backend pytest: 607 passed.
- `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.

Review status:

- #100 behavior parity, state/history safety, and integration/drift reviewers
  all returned PASS.
- #101 was rebased onto #100 and passed an integration/drift reviewer after the
  rebase.

Wave 7 handoff queue:

- #96 remains prep-only clarification evidence and must be split into smaller
  terminal PRs before production replay.
- #97 remains the strongest BoardTask explain extraction template and may be
  replayed next from fresh `main`.
- #98 remains prep-only confirmed-resource audit evidence. Activate its trace
  contract before any confirmed-resource extraction.
- Worker G1's ready-generation handler prep has been consumed by #101; do not
  merge the old prep branch.

Next production queue:

- Wave 8 slot A: BoardTask single-target explain extraction from fresh
  `c413a192e7805df95b14b86809afe661d5721dd1`.
- Wave 8 slot B: confirmed-resource generation trace activation from fresh
  `c413a192e7805df95b14b86809afe661d5721dd1`.
- Parallel preparation may continue for clarification split, edit/chat handler
  refresh, remaining generation-path contracts, and compatibility/drift guards.

Wave 7 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: extract board task write terminal` and
  `refactor: extract ready requirement generation terminal`.
- Do not start shared runtime/dependency cleanup yet. Dependency consolidation
  remains blocked until more leaf handlers are landed and real duplication is
  visible.

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
| F: BoardTask write trace | `codex/integrate/board-task-write-trace` | `030561358476e9c40bbe7bfbe9fb8d65b4e7457b` | write success, execution failure, no-changed-document failure, BoardPatch metadata, write commit, BoardTask consume, response trace | production merged via #94 as `b9361743e18e43fd7e9326cd0505dce4d9cb8442` | complete; #77 remains historical evidence only | replayed and merged |
| F2: BoardTask write extraction | `codex/integrate/board-task-write-extraction-wave7` | `6fe4e6a05f8ca8bed9e90bf8b94a135ca6fa55e7` | `_execute_board_task_write(...)` terminal moved to `chat/paths/board_task_write.py` | production merged via #100 as `9bae84d92a219f35b81d53a4cd3121c96306ff9b` | complete; write trace contract preserved | replayed and merged |
| G: BoardTask edit trace | `codex/integrate/board-task-edit-trace` | `65e0ef4fe920970ba1083eda2d54dd2041fb1cb8` | edit success, execution failure, no-changed-document failure, BoardPatch metadata, consume/save/response ordering | production merged via #91 as `100ff1e00e314e66c998e5258c476ebcd2654286` | complete; commit metadata behavior preserved from current main | replayed and merged |
| H: BoardTask explain trace | `codex/parallel/board-task-explain-trace` | `eb6d8cf01b81289775a0e9f1d00b78b80f3e9fe2` | single-target explanation, directive failure, sequence plan boundary, commit metadata, consume ordering | production merged via #86 as `73c0af289df3a49de1b4d7c6cb98d347f852bdbb` | complete | replayed and merged |
| S: sequence start extraction | `codex/integrate/sequence-start-extraction` | `1d5e409df752169c19464bf35acabdcbfa25fee9` | decided sequence session start terminal path | production merged via #92 as `ae82bf55075ba18cb6e9c27f38889f2051b37bea` | sequence lane complete | replayed and merged |
| I: ready requirement generation trace | `codex/integrate/ready-requirement-generation-trace` | `e307f82532b0078a22f3c21107e002593598e22f` | ready -> freeze -> BoardEditor -> commit -> consume; generation failure retryability contract | production merged via #95 as `6f9918e880d8cd451916cafe859726f667a1bef9` | complete; ready generation extraction may start in Wave 7 | replayed and merged |
| I2: ready requirement generation extraction | `codex/integrate/ready-requirement-generation-extraction-wave7` | `bf84bb33a5298151ba50f2457364c3c2e3cbb267` | regular ready requirement generation terminal moved to `chat/paths/ready_requirement_generation.py` | production merged via #101 as `c413a192e7805df95b14b86809afe661d5721dd1` | complete; explicit start, knowledge-board, and confirmed-resource generation remain separate | replayed and merged |
| J: confirmed-resource generation audit | `codex/prep/confirmed-resource-generation-audit-wave6` | `5a1c6b963728014aacb56cd148782f146a5311f9` | confirmed-resource generation durable-order audit and xfailed trace contract | prep-only PR #98 open; do not merge as production | activate trace contract after ready generation extraction | prep-only; not merged |
| L: BoardTask clarification handler prep | `codex/prep/board-task-clarification-handler-wave6-agent-a` | `eea84c40856621aee761c3ad53f700cd709e4e8e` | missing fields, clarify_location, unresolved edit conversion, await confirmation, decline terminal extraction candidate | prep-only PR #96 open; do not merge directly | split before production replay | prep-only; not merged |
| M: BoardTask explain handler prep | `codex/prep/agent-b-board-task-explain` | `ca8afcfb9b90a24c692c2bc3e92946e9845b67e2` | single-target BoardTask explain terminal extraction candidate | prep-only PR #97 open; do not merge directly | replay after write extraction and before edit extraction | prep-only; not merged |
| K: compatibility cleanup inventory | `codex/prep/compatibility-cleanup-wave5` | `c389147d15b7fd1bdb2509fc16c201a0bafcf333` | teaching_action, direct_edit, old document actions, fallback explain, recent edit follow-up, autonomous location choice, stale PRs | preparation-only docs inventory | no production PR | docs-only prep branch ready; not merged |

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
- G production replay in #91 preserved current-main edit commit metadata exactly;
  the consumed BoardTask stamp is reflected in response/run/event/PERSIST trace,
  not by rewriting the edit commit metadata after consume.
- The old `sequence-start-trace` / `board-task-explain-trace` ordering conflict
  was resolved by the #86 replay; `BOARD_SEQUENCE_START` is now an expected
  existing node.
- Future text conflicts are expected mainly around BoardTask explain / edit /
  clarification terminals and confirmed-resource generation in `chatbot.py`.
  Integrate by replaying one lane at a time, not by merging worker branches.
  For tests, add focused files instead of growing `test_workflow_trace.py`.
- No reviewer found domain hardcoding, new `NodeId` values, API/SSE/schema/prompt
  changes, or central-file scope creep outside candidate `chatbot.py` trace
  instrumentation.

## Integration Queue

1. Wave 8 slot A: BoardTask single-target explain extraction, using #97 as
   prep evidence and a fresh branch from
   `c413a192e7805df95b14b86809afe661d5721dd1`.
2. Wave 8 slot B: confirmed-resource generation trace activation, using #98 as
   prep evidence and a fresh branch from
   `c413a192e7805df95b14b86809afe661d5721dd1`.
3. Keep #96/#97/#98 draft and prep-only; replay manually instead of merging
   their branches directly.
4. Split #96 before production replay; do not migrate all clarification
   terminals in one PR.
5. Confirmed-resource extraction waits until its trace contract passes.
6. The sequence lane is complete after #92; no further main sequence migration
   PR is queued.
7. K remains docs-only compatibility cleanup inventory.
8. Do not start shared runtime/dependency cleanup yet.

## Next Production PR Scope

### Wave 8 Slot A: BoardTask Single-Target Explain Extraction

Fresh branch should start from `c413a192e7805df95b14b86809afe661d5721dd1`.

Own only:

- single-target BoardTask explain terminal
- `apps/api/app/services/chat/paths/board_task_explain.py`
- focused explain handler and trace parity tests

Testing note:

- Preserve #86 trace node order and commit metadata exactly.
- Do not touch sequence-start delegation, clarification, write, edit, chat,
  API, SSE, schema, prompt, or NodeId values.

### Wave 8 Slot B: Confirmed-Resource Generation Trace Activation

Fresh branch should start from `c413a192e7805df95b14b86809afe661d5721dd1`.

Own only the confirmed-resource generation trace contract:

- confirmed resource selection/provenance boundary
- requirement freeze before BoardEditor
- generation failure event and retryable frozen run
- success commit, consume, save, and response trace

Testing note:

- Use #98's xfailed contract as evidence, but do not merge the old prep branch
  directly.
- Do not extract the confirmed-resource handler in the same PR.
- Do not touch regular ready generation, explicit `board_generation_action=start`,
  knowledge-board generation, API, SSE, schema, prompt, or NodeId values.

### Wave 9 Prep P11: KnowledgeBoard Minimal Generation Handler Proposal

Fresh branch starts exactly from
`738b378aff760d27e38a6130254f2a6a0e73b99b`.

Owned files in this prep lane:

- `apps/api/app/services/chat/paths/knowledge_board_generation.py`
- `apps/api/tests/board_task/test_knowledge_board_generation_handler.py`
- this maintenance handoff section

Non-goals:

- no `chatbot.py` production call-site commit in the prep lane
- no explicit `board_generation_action=start` handling
- no generation-control route handling
- no confirmed-resource or resource-prompt handling
- no shared/common generation engine extraction
- no API, SSE, schema, prompt, or `NodeId` changes

Trigger / precedence contract:

- The handler owns only an already-classified
  `InitialLearningWorkModeDecision(work_mode="knowledge_board")`.
- The caller still owns the outer precedence order: blank-board check,
  initial-learning signal check, resource prompt / confirmed-resource exclusion,
  and separate explicit start / generation-control lanes.
- `unknown` and `narrow_topic` remain delegated to
  `handle_initial_guidance(...)`.
- `practice_artifact` still falls through to the regular requirement collection
  path.
- Confirmed resource context and resource prompt context are rejected before any
  dependency is called, so this lane cannot consume confirmed-resource evidence
  by accident.

Freeze checkpoint contract:

- Minimal requirements are constructed with `generate_board=True`, then stamped
  with `action_type="generate_board"`.
- `_prepare_initial_requirement_for_board_generation(...)` must produce the
  frozen requirement before BoardEditor is called.
- `_checkpoint_initial_requirement_before_generation(...)` must persist the
  frozen run/version to SQLite before `generate_from_requirements(...)`.
- This minimal lane does not require a prior `completed` requirement version;
  a fresh run can have `created -> frozen -> consumed` events on success.

Failure retryability contract:

- If BoardEditor returns `changed=False`, no lesson commit is written.
- A `generation_failed` event is appended against the frozen version.
- The active run stays `frozen` with no `consumed_commit_id`, so the same frozen
  version remains retryable.
- The response carries the failed operation status and failure reason.

Metadata contract on success:

- `kind="board_document_generation"`
- `board_generation_action="knowledge_board_minimal_requirement"`
- `initial_learning_work_mode.work_mode="knowledge_board"`
- `task_requirement_sheet.work_mode="knowledge_board"`
- `requirement_run_id`, `frozen_requirement_version_id`,
  `requirement_phase="frozen"`, and
  `requirement_run_status_after_commit="consumed"`
- board quality metadata, board edit operation/summary/section titles, and
  `resource_resolution_status`

Trace gap in current inline path:

- Current main already freezes, generates, commits, consumes, saves, and
  responds, but the `knowledge_board` inline body does not record the target
  trace sequence.
- The prep handler records:
  `INITIAL_MODE_DECIDE -> INITIAL_REQUIREMENT_FREEZE ->
  INITIAL_BOARD_GENERATE -> INITIAL_BOARD_COMMIT -> RESPONSE_ASSEMBLE`.
- On failure it records:
  `INITIAL_MODE_DECIDE -> INITIAL_REQUIREMENT_FREEZE ->
  INITIAL_BOARD_GENERATE -> INITIAL_GENERATION_FAILED -> RESPONSE_ASSEMBLE`.

Standalone handler proposal:

- `handle_knowledge_board_minimal_generation(...)` mirrors the current inline
  terminal behavior, but keeps all dependencies injected for focused tests.
- It deliberately does not import or call the initial work-mode classifier; the
  central caller passes in the already-produced decision.
- It deliberately does not accept `reference_context`; confirmed-resource
  generation remains a separate lane.

Focused tests:

- success: freezes before BoardEditor, commits, consumes, saves, assembles the
  response, and preserves commit metadata
- failure: persists a retryable frozen run, writes `generation_failed`, avoids a
  lesson commit, saves, and assembles the response
- trigger rejection: non-`knowledge_board`, resource prompt, and confirmed
  resource context stop before side effects or trace records

Exact central call-site replacement for the integrator:

```python
from app.services.chat.paths.knowledge_board_generation import (
    KnowledgeBoardMinimalGenerationDependencies,
    handle_knowledge_board_minimal_generation,
)
```

Replace only the current inline `knowledge_board` body inside
`_handle_initial_learning_work_mode(...)`, starting after:

```python
if decision.work_mode != "knowledge_board":
    return None
```

with:

```python
return handle_knowledge_board_minimal_generation(
    workspace=workspace,
    package=package,
    lesson=lesson,
    user_id=user_id,
    request=request,
    requirements=requirements,
    decision=decision,
    resource_summary_for_turn=resource_summary_for_turn,
    resource_resolution=resource_resolution,
    selected_reference=selected_reference,
    requirement_history=requirement_history,
    track_initial_requirement_run=track_initial_requirement_run,
    deps=KnowledgeBoardMinimalGenerationDependencies(
        minimal_initial_learning_state=_minimal_initial_learning_state,
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
        initial_learning_work_mode_metadata=_initial_learning_work_mode_metadata,
        reference_metadata=_reference_metadata,
        save_workspace_for_user=_save_workspace_for_user,
        build_response=_response,
    ),
)
```

## Repair Queue

Repair-only branches should not be merged directly:

- G: production merged via #91; do not reopen consumed-phase commit metadata
  changes without a separate explicit fix PR.
- F: production merged via #94; write extraction merged via #100.
- S: production merged via #92; sequence lane complete.
- I: production merged via #95; ready generation extraction merged via #101.
- J: confirmed-resource audit is open as prep-only #98; activate trace next.
- L/M: #96/#97 are prep-only handler evidence and must be manually replayed.
- K: docs-only compatibility cleanup inventory.

## Notes

- The old preparation branches are useful as specs, tests, and candidate
  patches, but they are not merge branches.
- The next production work should start from
  `c413a192e7805df95b14b86809afe661d5721dd1`, not from the old parallel-wave
  base or Wave 7 branches.

# Chatbot Parallel Migration Registry

This document records the parallel preparation wave for the `chatbot.py`
strangler migration and the current production integration plan. It is a
maintainer-facing coordination record, not an executable workflow.

## Current Base

- Integration base branch: `origin/main`
- Integration base SHA: `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`
- Latest verified `MAIN_SHA`: `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`
- Wave 10 production base SHA: `e71a5c9168db37ef126ccbb8f574359f840e258f`
- Parallel-wave evidence branches remain evidence only unless replayed manually
  from the latest verified `main`.
- PR #110 / Explicit API start generation extraction: rebased after #109 and
  merged into `main` as `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.
- PR #109 / BoardTask missing-fields terminal extraction: merged into `main` as
  `51f6de1e13caa2e6152b8c280c858862debe30df`.
- PR #108 / Wave 9 checkpoint docs: merged into `main` as
  `e71a5c9168db37ef126ccbb8f574359f840e258f`.
- PR #107 / Confirmation decline terminal extraction: rebased after #106 and
  merged into `main` as `3981149ca251d691ab43fe95a6b7723199f8951a`.
- PR #106 / Confirmed-resource generation extraction: merged into `main` as
  `72387db3c42214e7a1394890f6a1bcba2f3c39b8`.
- PR #105 / Wave 8 checkpoint docs: merged into `main` as
  `555e4ca8214c84f878d5488be01ebd4969db6aa7`.
- PR #104 / Confirmed-resource trace activation: rebased after #103 and merged
  into `main` as `738b378aff760d27e38a6130254f2a6a0e73b99b`.
- PR #103 / BoardTask explain extraction: merged into `main` as
  `79c7839648703931028bdbf617ba97944315e7b9`.
- PR #102 / Wave 7 checkpoint docs: merged into `main` as
  `c2bef6be6a6da387025116a3ff8f8ec740b12b15`.
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

## Wave 8 Checkpoint

Wave 8 started from `MAIN_SHA`
`c2bef6be6a6da387025116a3ff8f8ec740b12b15` and closed production at
`738b378aff760d27e38a6130254f2a6a0e73b99b`. Its checkpoint docs closed at
`555e4ca8214c84f878d5488be01ebd4969db6aa7`.

Merged production status:

- #103 `refactor: extract board task explanation terminal`: merged as
  `79c7839648703931028bdbf617ba97944315e7b9`; main push Verify
  #27877574666 succeeded.
- #104 `refactor: trace confirmed resource generation activation`: rebased
  after #103, merged as `738b378aff760d27e38a6130254f2a6a0e73b99b`; main push
  Verify #27889316002 succeeded.

Coordinator verification:

- #103 focused explain handler/trace set: 11 passed.
- #103 BoardTask suite: 353 passed.
- #103 AI logging regression suite: 127 passed.
- #103 full backend pytest: 612 passed.
- #103 `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.
- #104 post-rebase focused confirmed-resource trace set: 8 passed.
- #104 post-rebase BoardTask suite: 357 passed.
- #104 post-rebase AI logging regression suite: 127 passed.
- #104 post-rebase full backend pytest: 616 passed.
- #104 post-rebase `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.

Review status:

- #103 behavior parity and state/history reviewers returned PASS.
- #103 integration/drift reviewer initially failed on a duplicate dead
  `_board_task_explanation_target_excerpt` helper left in `chatbot.py`; the
  helper was removed and R3 re-review returned PASS.
- #104 behavior parity and state/history reviewers returned PASS.
- #104 integration/drift reviewer initially failed because the existing
  `RESOURCE_CONFIRMED_GENERATE` node was missing from the new trace contract;
  the node and tests were added, the stale base label was corrected, and R3
  re-review returned PASS. A post-rebase R3 reviewer also returned PASS.

Prep handoff status:

- P1 confirmed-resource extraction prep:
  `codex/prep/confirmed-resource-extraction-wave8-c2be` at
  `83a8dbcbe0833c0eaaefe201a9843bc8dc38de9e`; prep-only, no `chatbot.py`
  wiring.
- P2 confirmation decline terminal prep:
  `codex/prep/board-task-confirmation-decline-wave8-c2be` at
  `0a3b298887092e2a82f4aded01e49c57459fcae9`; prep-only.
- P3 missing-fields terminal prep:
  `codex/prep/board-task-missing-fields-wave8-c2be` at
  `0531a22531c4e307fc78585f7a515b691f04f663`; prep-only.
- P4 await-write-confirmation terminal prep:
  `codex/prep/board-task-await-write-confirmation-wave8-c2be` at
  `d043d1e3ae1f802b2211773900dfb9d4690de89f`; prep-only.
- P5 unresolved-edit conversion prep:
  `codex/prep/board-task-unresolved-edit-conversion-wave8-c2be` at
  `24bc0e9bde455be5439418dda2ef711392a119bd`; prep-only.
- P6 normal location clarification prep:
  `codex/prep/board-task-location-clarification-wave8-c2be` at
  `d209a24cf4c9d29c00f936c1cc390a91c456bf8a`; prep-only.
- P7 BoardTask edit refresh prep:
  `codex/prep/board-task-edit-refresh-wave9-738b` at
  `cfccc4f81f4e34dbaf03b5eefa3f887cea4fb23b`; prep-only, no `chatbot.py`
  wiring.
- P8-P12 were launched from
  `738b378aff760d27e38a6130254f2a6a0e73b99b` after Wave 8 production closed:
  chat handoff refresh, explicit API start generation, generation-control,
  knowledge-board minimal generation, and compatibility audit refresh. They
  remain preparation-only until handoff review.

Wave 8 handoff queue:

- Confirmed-resource generation extraction was completed in Wave 9 via #106.
- Confirmation decline terminal extraction was completed in Wave 9 via #107.
- Later waves should continue one terminal/path at a time: missing fields,
  await-write-confirmation, unresolved-edit conversion, normal location
  clarification, BoardTask edit, BoardTask chat handoff, then the remaining
  generation paths.

Wave 8 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: extract board task explanation terminal` and
  `refactor: trace confirmed resource generation activation`.
- #96/#97/#98 and all prep branches remain evidence only. Do not directly
  merge old prep branches.
- Continue using focused test files for new path contracts.

## Wave 9 Checkpoint

Wave 9 started from `MAIN_SHA`
`555e4ca8214c84f878d5488be01ebd4969db6aa7` and closed production at
`3981149ca251d691ab43fe95a6b7723199f8951a`.

Merged production status:

- #106 `refactor: extract confirmed resource generation path`: merged as
  `72387db3c42214e7a1394890f6a1bcba2f3c39b8`; PR Verify #27895670310 and
  main push Verify #27895735028 succeeded.
- #107 `refactor: extract board task confirmation decline path`: rebased after
  #106, merged as `3981149ca251d691ab43fe95a6b7723199f8951a`; PR Verify
  #27895878943 and main push Verify #27895929066 succeeded.

Coordinator verification:

- #106 focused confirmed-resource generation set: 22 passed.
- #106 BoardTask suite: 361 passed.
- #106 AI logging regression suite: 127 passed.
- #106 full backend pytest: 620 passed.
- #106 `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.
- #107 post-rebase focused confirmation-decline set: 46 passed.
- #107 post-rebase BoardTask suite: 364 passed.
- #107 post-rebase AI logging regression suite: 127 passed.
- #107 post-rebase full backend pytest: 623 passed.
- #107 post-rebase `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.

Review status:

- #106 behavior parity, state/history safety, and integration/drift reviewers
  all returned PASS.
- #107 behavior parity, state/history safety, and integration/drift reviewers
  all returned PASS before rebase. A post-rebase integration/drift reviewer
  also returned PASS and verified that the #106 confirmed-resource imports,
  dependency factory, and call sites survived conflict resolution.

Prep and audit handoff status:

- C1 missing-fields refresh:
  `codex/prep/board-task-missing-fields-refresh-wave9-555e` at
  `34334cc6176a73f3f1fa158a1ad6ea0c4d0c39f`; prep-only, no forbidden files.
- C2 await-write-confirmation refresh:
  `codex/prep/await-write-confirmation-refresh-wave9-555e` at
  `94673fa8e023edaf2a5e1ea84bf8581b8738c063`; prep-only.
- C3 unresolved-edit versus normal location clarification audit: read-only PASS.
  Production order should be unresolved edit conversion first, then normal
  location clarification.
- G1 explicit API start audit: read-only PASS. Future production replay should
  replace only the `board_generation_action == "start"` call site and must not
  swallow confirmed-resource, ready requirement generation, or existing-board
  API start behavior.
- G2 text-triggered generation split:
  `codex/prep/text-triggered-generation-split-wave9-555e` at
  `53212d0c74e89fa34c536870582b7fdeda9a35e5`; prep-only classifier plus
  terminal proposal.
- G3 knowledge-board minimal audit: read-only PASS. Future replay must avoid
  duplicate `INITIAL_MODE_DECIDE` and must reject resource-confirmed/resource
  prompt paths.
- E BoardTask edit refresh audit: read-only PASS with follow-up test gaps for
  whole-document positive permission and recent-focus metadata.
- H BoardTask chat handoff audit: read-only PASS. Keep the future adapter thin:
  BoardTask chat decision -> build task requirements -> delegate to
  InteractionSession start.
- P8 chat handoff prep remains at
  `c66ef4eb605b7fa40235a7c2cb1ba55774e587e6`.
- P9 explicit API start prep remains at
  `097f128566cbb40996e6e13d5da2dd5ab4255246`.
- P10 generation-control prep remains at
  `b0bbde3be9555062a7df2519562fce90b737899f`.
- P11 knowledge-board minimal generation prep remains at
  `5d7b6ee672de16a0338b6945224ffe9b8ff9bfee`.
- P12 compatibility audit remains at
  `99e2f37ce12324faf4883bfffe7a5330c73e4506`.

Wave 9 handoff queue:

- BoardTask missing-fields terminal extraction was completed in Wave 10 via
  #109.
- Explicit API start generation extraction was completed in Wave 10 via #110.
- Await-write-confirmation, unresolved edit conversion, normal location
  clarification, text-triggered generation, knowledge-board minimal generation,
  edit extraction, and chat handoff remain preparation-only until their
  promotion audits pass from the latest main.

Wave 9 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: extract confirmed resource generation path` and
  `refactor: extract board task confirmation decline path`.
- P1/P2 were used only as evidence. They were not merged or cherry-picked.
- No shared generation engine was introduced. Defer shared generation
  dependencies until confirmed-resource, API start, text-triggered generation,
  and knowledge-board minimal generation are all leaf handlers on `main`.

## Wave 10 Checkpoint

Wave 10 started from `MAIN_SHA`
`e71a5c9168db37ef126ccbb8f574359f840e258f` and closed production at
`f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.

Merged production status:

- #109 `refactor: extract board task missing-fields terminal`: merged as
  `51f6de1e13caa2e6152b8c280c858862debe30df`; PR Verify #27902216883 and
  main push Verify #27902304213 succeeded.
- #110 `refactor: extract generation api start path`: rebased after #109,
  merged as `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`; post-rebase PR Verify
  #27902354916 and main push Verify #27902400608 succeeded.

Coordinator verification:

- #109 focused missing-fields handler set: 4 passed.
- #109 missing-fields workflow trace set: 2 passed.
- #109 BoardTask suite: 368 passed.
- #109 AI logging regression suite: 127 passed.
- #109 full backend pytest: 627 passed.
- #109 `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.
- #110 post-rebase focused API-start and neighboring contract set: 27 passed.
- #110 post-rebase BoardTask suite: 374 passed.
- #110 post-rebase AI logging regression suite: 127 passed.
- #110 post-rebase full backend pytest: 633 passed.
- #110 post-rebase `npm run verify`: passed with the pre-existing
  `word-board-editor.tsx` file-size warning.

Review status:

- #109 behavior parity, state/history safety, and integration/drift safety all
  returned PASS.
- #110 behavior parity, state/history safety, and integration/drift safety all
  returned PASS. Post-rebase R3 verified that #109's missing-fields import,
  dependency factory, and call site survived the conflict resolution.
- #110 preserved existing explicit API-start trace scope; it did not introduce
  initial generation trace nodes or a shared generation engine.
- Existing-board `board_generation_action="start"` remains outside the new
  API-start handler and keeps the no-overwrite/no-AI-call behavior.

Prep handoff status:

- C2 await-write-confirmation refresh:
  `codex/prep/await-write-confirmation-refresh-wave10-e71a` at
  `711a8715ab34b2cf35642d6ecff8f241f9cf63fb`; prep-only, no forbidden files.
- C3 unresolved-edit conversion refresh:
  `codex/prep/unresolved-edit-conversion-refresh-wave10-e71a` at
  `372ffc0cd34c65c4fb9928dd0cc43f3306e2cc91`; prep-only, no forbidden files.
- C4 normal location clarification refresh:
  `codex/prep/normal-location-clarification-refresh-wave10-e71a` at
  `a6c25920a08222f29a036c6cd80aba5cd539c3c9`; prep-only, no forbidden files.
- G2 text-trigger generation refresh:
  `codex/prep/text-trigger-generation-refresh-wave10-e71a` at
  `e5d90da9b86393a4cc8a1ea202e7e6da423d3259`; prep-only classifier and
  terminal candidate.
- G3 knowledge-board minimal refresh:
  `codex/prep/knowledge-board-minimal-refresh-wave10-e71a` at
  `71a2b95a1ef71cbbcf952b95ad3740fc80314a1a`; prep-only and keeps
  `INITIAL_MODE_DECIDE` ownership with the caller.
- E BoardTask edit test gaps:
  `codex/prep/board-task-edit-test-gaps-wave10-e71a` at
  `58071de900b5bc2330ed06e2a2df4ae2348814ea`; prep-only focused tests for
  whole-document edit authorization and recent-focus metadata precision.
- H BoardTask chat handoff refresh:
  `codex/prep/board-task-chat-handoff-refresh-wave10-e71a` at
  `67f849f93e0e05d12f7d0e7d92f3c7afdb8b9df6`; prep-only thin adapter that
  delegates InteractionSession ownership.
- K legacy compatibility matrix:
  `codex/prep/legacy-compatibility-matrix-wave10-e71a` at
  `43f26b2ad435914a1760714ca5c4336acab662f0`; docs-only matrix.

Next production queue:

- Wave 11 slot A: BoardTask await-write-confirmation terminal extraction from
  fresh `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`, using C2 as evidence.
- Wave 11 slot B: text-triggered generation classifier plus terminal extraction
  from fresh `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`, using G2 as evidence.
- Keep unresolved-edit conversion, normal location clarification,
  knowledge-board minimal generation, BoardTask edit extraction, BoardTask chat
  handoff, and legacy compatibility cleanup preparation-only until promoted.

Wave 10 production PRs:

- Opened and merged exactly two production PRs:
  `refactor: extract board task missing-fields terminal` and
  `refactor: extract generation api start path`.
- C1/P9/G1 were used only as evidence. They were not merged or cherry-picked.
- No shared runtime/dependency cleanup was started.

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
| G2: BoardTask edit extraction prep | `codex/prep/board-task-edit-test-gaps-wave10-e71a` | `58071de900b5bc2330ed06e2a2df4ae2348814ea` | whole-document edit authorization and recent-focus metadata precision tests | prep-only; no `chatbot.py` wiring; Wave 10 test gaps filled | waits behind clarification terminal queue | prep-only |
| H: BoardTask explain trace | `codex/parallel/board-task-explain-trace` | `eb6d8cf01b81289775a0e9f1d00b78b80f3e9fe2` | single-target explanation, directive failure, sequence plan boundary, commit metadata, consume ordering | production merged via #86 as `73c0af289df3a49de1b4d7c6cb98d347f852bdbb` | complete | replayed and merged |
| H2: BoardTask explain extraction | `codex/integrate/board-task-explain-extraction-wave8` | `11a0efd74ba3c23591dbd903e756c17c95696533` | single-target BoardTask explain terminal moved to `chat/paths/board_task_explain.py` | production merged via #103 as `79c7839648703931028bdbf617ba97944315e7b9` | complete; duplicate legacy excerpt helper removed before merge | replayed and merged |
| S: sequence start extraction | `codex/integrate/sequence-start-extraction` | `1d5e409df752169c19464bf35acabdcbfa25fee9` | decided sequence session start terminal path | production merged via #92 as `ae82bf55075ba18cb6e9c27f38889f2051b37bea` | sequence lane complete | replayed and merged |
| I: ready requirement generation trace | `codex/integrate/ready-requirement-generation-trace` | `e307f82532b0078a22f3c21107e002593598e22f` | ready -> freeze -> BoardEditor -> commit -> consume; generation failure retryability contract | production merged via #95 as `6f9918e880d8cd451916cafe859726f667a1bef9` | complete; ready generation extraction may start in Wave 7 | replayed and merged |
| I2: ready requirement generation extraction | `codex/integrate/ready-requirement-generation-extraction-wave7` | `bf84bb33a5298151ba50f2457364c3c2e3cbb267` | regular ready requirement generation terminal moved to `chat/paths/ready_requirement_generation.py` | production merged via #101 as `c413a192e7805df95b14b86809afe661d5721dd1` | complete; explicit start, knowledge-board, and confirmed-resource generation remain separate | replayed and merged |
| J: confirmed-resource generation audit | `codex/prep/confirmed-resource-generation-audit-wave6` | `5a1c6b963728014aacb56cd148782f146a5311f9` | confirmed-resource generation durable-order audit and xfailed trace contract | prep-only PR #98 open; do not merge as production | activate trace contract after ready generation extraction | prep-only; not merged |
| J2: confirmed-resource trace activation | `codex/integrate/confirmed-resource-trace-activation-wave8` | `daea41763d07ba92f907a51e203d92e2b2cc7155` | confirmed-resource generation trace contract, including existing `RESOURCE_CONFIRMED_GENERATE` and initial generation nodes | production merged via #104 as `738b378aff760d27e38a6130254f2a6a0e73b99b` | complete; handler extraction completed via #106 | replayed and merged |
| J3: confirmed-resource extraction | `codex/integrate/confirmed-resource-generation-extraction-wave9` | `a0d1e371988488a3ec2b67e3e73fcc0326060886` | confirmed-resource generation terminal moved to `chat/paths/confirmed_resource_generation.py` | production merged via #106 as `72387db3c42214e7a1394890f6a1bcba2f3c39b8` | complete; #104 trace contract preserved; no shared generation engine | replayed and merged |
| J4: explicit API start generation extraction | `codex/integrate/generation-api-start-wave10` | `86bdbe091507a304051c2da535f2c843ad8a6a6b` | explicit `board_generation_action == "start"` generation terminal moved to `chat/paths/generation_api_start.py` | production merged via #110 as `f005b8d1a3912a96c09cf5d499e665338ec8e9f0` | complete; existing-board API start remains outside handler | replayed and merged |
| J5: text-triggered generation split prep | `codex/prep/text-trigger-generation-refresh-wave10-e71a` | `e5d90da9b86393a4cc8a1ea202e7e6da423d3259` | pure text-trigger classifier plus terminal handler proposal | prep-only; no `chatbot.py` wiring | Wave 11 slot B candidate | prep-only |
| J6: knowledge-board minimal generation prep | `codex/prep/knowledge-board-minimal-refresh-wave10-e71a` | `71a2b95a1ef71cbbcf952b95ad3740fc80314a1a` | minimal `knowledge_board` generation handler proposal | prep-only; G3 refresh complete | avoid duplicate `INITIAL_MODE_DECIDE`; waits behind text-triggered generation | prep-only |
| L: BoardTask clarification handler prep | `codex/prep/board-task-clarification-handler-wave6-agent-a` | `eea84c40856621aee761c3ad53f700cd709e4e8e` | missing fields, clarify_location, unresolved edit conversion, await confirmation, decline terminal extraction candidate | prep-only PR #96 open; do not merge directly | split before production replay | prep-only; not merged |
| L2: confirmation decline extraction | `codex/integrate/board-task-confirmation-decline-wave9` | `d88785f267a73f9a8ea3adc674d53b8e114a9d2d` | awaiting write confirmation decline terminal moved to `chat/paths/board_task_confirmation_decline.py` | production merged via #107 as `3981149ca251d691ab43fe95a6b7723199f8951a` | complete; post-rebase R3 PASS | replayed and merged |
| L3: missing fields extraction | `codex/integrate/board-task-missing-fields-wave10` | `27e9c21907947a738d2c4c642496f05c9e02766e` | missing BoardTask fields terminal moved to `chat/paths/board_task_missing_fields.py` | production merged via #109 as `51f6de1e13caa2e6152b8c280c858862debe30df` | complete; `BOARD_TASK_COLLECT` remains caller-owned | replayed and merged |
| L4: await write confirmation prep | `codex/prep/await-write-confirmation-refresh-wave10-e71a` | `711a8715ab34b2cf35642d6ecff8f241f9cf63fb` | await-write-confirmation terminal | prep-only; C2 refresh complete | Wave 11 slot A candidate | prep-only |
| L5: unresolved edit conversion prep | `codex/prep/unresolved-edit-conversion-refresh-wave10-e71a` | `372ffc0cd34c65c4fb9928dd0cc43f3306e2cc91` | unresolved edit to write confirmation conversion | prep-only; C3 refresh complete | must land before normal location clarification | prep-only |
| L6: normal location clarification prep | `codex/prep/normal-location-clarification-refresh-wave10-e71a` | `a6c25920a08222f29a036c6cd80aba5cd539c3c9` | normal missing/ambiguous BoardTask location clarification | prep-only; C4 refresh complete | waits behind unresolved edit conversion boundary | prep-only |
| L7: BoardTask chat handoff prep | `codex/prep/board-task-chat-handoff-refresh-wave10-e71a` | `67f849f93e0e05d12f7d0e7d92f3c7afdb8b9df6` | thin BoardTask chat handoff adapter | prep-only; H refresh complete | keep InteractionSession start ownership outside adapter | prep-only |
| M: BoardTask explain handler prep | `codex/prep/agent-b-board-task-explain` | `ca8afcfb9b90a24c692c2bc3e92946e9845b67e2` | single-target BoardTask explain terminal extraction candidate | prep-only PR #97 open; do not merge directly | replay after write extraction and before edit extraction | prep-only; not merged |
| K: compatibility cleanup inventory | `codex/prep/legacy-compatibility-matrix-wave10-e71a` | `43f26b2ad435914a1760714ca5c4336acab662f0` | teaching_action, direct_edit, old document actions, fallback explain, private aliases, recent edit follow-up, autonomous location choice, stale PRs | preparation-only docs inventory | no production PR | docs-only prep branch ready; not merged |

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
- #103 moved single-target BoardTask explain into
  `chat/paths/board_task_explain.py`; R3 required removal of the old duplicate
  `_board_task_explanation_target_excerpt` helper in `chatbot.py` before merge.
- #104 activated the confirmed-resource generation trace contract and records
  the existing `RESOURCE_CONFIRMED_GENERATE` node before freeze/generate
  boundaries. No new `NodeId` was introduced.
- #106 moved confirmed-resource generation into
  `chat/paths/confirmed_resource_generation.py`, preserved the #104 trace
  order including `RESOURCE_CONFIRMED_GENERATE`, and did not introduce a shared
  generation engine.
- #107 moved awaiting write-confirmation decline into
  `chat/paths/board_task_confirmation_decline.py`. The post-rebase R3 review
  verified that #106's confirmed-resource imports, dependency factory, and call
  sites were preserved after conflict resolution.
- C3 clarified the next BoardTask clarification split order: unresolved edit
  conversion must land before normal location clarification so the two handlers
  do not compete for the same `clarify_location` route.
- G1 clarified the next generation split order: explicit API start should
  replace only the existing `board_generation_action == "start"` call site and
  must not swallow confirmed-resource, ready requirement, or existing-board API
  start behavior.
- No reviewer found domain hardcoding, new `NodeId` values, API/SSE/schema/prompt
  changes, or central-file scope creep outside candidate `chatbot.py` trace
  instrumentation.

## Integration Queue

1. Wave 11 slot A: BoardTask await-write-confirmation terminal extraction,
   using C2 Wave 10 prep as evidence and a fresh branch from
   `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.
2. Wave 11 slot B: text-triggered generation classifier plus terminal
   extraction, using G2 Wave 10 prep as evidence and a fresh branch from
   `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.
3. Continue the split clarification queue after await-write-confirmation:
   unresolved edit conversion, then normal location clarification.
4. Continue the generation queue after text-triggered generation:
   knowledge-board minimal generation, then confirmed-resource follow-up cleanup
   if needed.
5. Keep #96/#97/#98 draft and prep-only; replay manually instead of merging
   their branches directly.
6. K/P12 and the Wave 10 K refresh remain docs-only compatibility cleanup
   inventory.
7. Do not start shared runtime/dependency cleanup yet.

## Next Production PR Scope

### Wave 11 Slot A: BoardTask Await-Write-Confirmation Terminal Extraction

Fresh branch should start from `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.

Own only:

- BoardTask await-write-confirmation terminal
- `apps/api/app/services/chat/paths/board_task_await_write_confirmation.py`
- focused await-write-confirmation handler and direct/integration parity tests

Testing note:

- Caller must still own route decision and content-absent confirmation
  precedence.
- Handler must not execute the confirmed write or the confirmation-decline path.
- Confirmation decline extracted in #107 must keep its precedence and behavior.
- Save or response-build failure must not record false terminal or response
  trace nodes.
- Do not touch unresolved edit conversion, normal location clarification, write,
  edit, explain, chat, API, SSE, schema, prompt, or NodeId values.

### Wave 11 Slot B: Text-Triggered Generation Extraction

Fresh branch should start from `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`.

Own only:

- text-triggered generation classifier
- text-triggered generation terminal
- `apps/api/app/services/chat/paths/generation_text_trigger.py`
- `apps/api/app/services/chat/paths/text_triggered_generation.py`
- focused classifier, handler, and precedence tests

Testing note:

- Classifier must reject `board_generation_action == "start"`,
  resource-reference actions, resource prompts, non-empty board documents, and
  unactionable continue-like messages.
- Do not swallow confirmed-resource generation, ready requirement generation,
  explicit API start, knowledge-board generation, or resource-confirmed paths.
- Preserve freeze/generate/failure/commit/consume/response ordering and current
  metadata semantics for text-triggered generation.
- Do not introduce a shared generation engine or new NodeId values.

## Repair Queue

Repair-only branches should not be merged directly:

- G: production merged via #91; do not reopen consumed-phase commit metadata
  changes without a separate explicit fix PR.
- F: production merged via #94; write extraction merged via #100.
- S: production merged via #92; sequence lane complete.
- I: production merged via #95; ready generation extraction merged via #101.
- J: confirmed-resource trace is production merged via #104; confirmed-resource
  extraction is production merged via #106. API start extraction is production
  merged via #110. Text-triggered generation is the next generation production
  candidate.
- L: #96 is split into P2-P6 prep branches. P2 is consumed by #107; C1 missing
  fields is consumed by #109. C2 await-write-confirmation is the next BoardTask
  clarification production candidate.
- M: #97 has been consumed by #103. Keep it as historical evidence only.
- K/P12: docs-only compatibility cleanup inventory and guard design; do not
  merge broad guard until canonical explain/edit/chat/generation paths land.

## Notes

- The old preparation branches are useful as specs, tests, and candidate
  patches, but they are not merge branches.
- The next production work should start from
  `f005b8d1a3912a96c09cf5d499e665338ec8e9f0`, not from old Wave 8, Wave 9, or Wave 10
  prep branches.

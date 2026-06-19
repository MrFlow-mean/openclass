# Lane A Audit: Sequence Continue Extraction

Audit date: 2026-06-20

Lane: `sequence_continue_extraction`

Worktree: `/Users/liqianhao/Desktop/openclass-worktrees/sequence-continue-extraction`

Branch: `codex/parallel/sequence-continue-extraction`

Base audited SHA: `cb004788511748a01dd8e76604425616b8f012f6`

Branch head before this report: `cb004788511748a01dd8e76604425616b8f012f6`

Scope: audit only. No production extraction, no runtime behavior change.

## Classification

Demand: audit current sequence continuation behavior and prepare a blocked extraction plan.

Files changed by this lane: this report only.

Problem category:

- [x] General product capability
- [ ] Content-shape abstraction
- [ ] Prompt quality issue
- [ ] Schema / data-structure issue
- [ ] UI interaction issue
- [ ] Resource parsing issue
- [ ] Specific textbook adapter
- [ ] Demo / test sample

Hard-code review:

- [ ] Subject keyword
- [ ] Textbook keyword
- [ ] Fixed HTML
- [ ] Fixed lecture content
- [ ] Demo content
- [ ] Branch for one test sample

Result: this lane is generic workflow audit work. It adds no subject, textbook, exam, or demo-specific logic.

## Blocker

PR #79 is still a Draft pull request: `refactor: extract sequential interaction exit and completion paths`.

Public GitHub page checked on 2026-06-20:

<https://github.com/MrFlow-mean/openclass/pull/79>

The PR summary says it extracts only explicit sequence exit and natural completion terminals, keeps `_handle_section_explanation_sequence_turn(...)` as the route/classification owner, and intentionally leaves current-unit follow-up and advance in legacy `chatbot.py`. Therefore Lane A should not integrate production continuation extraction until #79 is merged into `main` or explicitly excluded from the stack.

`gh pr view 79` could not be used in this worktree because GitHub CLI (GitHub command-line client) is not authenticated.

## Owned Symbols

Lane A owns only the continuation decisions inside `_handle_section_explanation_sequence_turn(...)`:

- `follow_up_current`
- `advance`

Lane A must not own these decisions in this blocked audit lane:

- `exit_requested`
- `completed`
- unrecognized sequence input / `not_handled`
- generic active `InteractionSession` routes
- sequence start
- BoardTask collection, write, edit, explain, or chat routes

## Current Main Behavior

Source files inspected:

- `AGENTS.md`
- `docs/architecture/chat-workflow-graph.md`
- `apps/api/app/services/chatbot.py`
- `apps/api/app/services/workflow_trace.py`
- existing `apps/api/app/services/chat/paths/*`
- `apps/api/tests/board_task/test_interaction_sequence_trace.py`
- adjacent interaction handoff/fallback/empty/start trace tests

Current main already records the sequence continuation trace, but continuation execution still lives in `chatbot.py`.

### Common Trace Prefix

Both owned decisions are reached only after the active interaction check:

```text
CONTEXT_LOAD
-> TURN_CONTEXT_BUILD
-> BOARD_ACTION_DECIDE
-> CHAT_TURN_GATE
-> RESOURCE_PREFLIGHT
-> ACTIVE_INTERACTION_CHECK
-> INTERACTION_SEQUENCE_CHECK
```

`_handle_existing_interaction_session(...)` records `INTERACTION_SEQUENCE_CHECK decision="not_handled"` only after `_handle_section_explanation_sequence_turn(...)` returns `None`. The handled `follow_up_current` and `advance` paths record exactly one sequence check and do not call generic active-interaction handlers.

## Decision: `follow_up_current`

Current trigger:

- Active `InteractionSession` has `sequence_mode in {"section_explanation", ATOMIC_EXPLANATION_SEQUENCE_MODE}` and non-empty `sequence_items`.
- User message is not a sequence continue message.
- User message is recognized as a current-sequence follow-up by `_is_current_sequence_followup(...)`.

Current state transition:

- `focus = session_before.target_focus or session_before.sequence_items[session_before.sequence_index]`
- `sequence_index` is unchanged.
- `target_focus` becomes `focus`.
- `reference_context` becomes `focus_context(focus)`.
- `turn_count` increments by 1.
- `status` becomes `"active"`.
- `pause_reason` becomes `""`.
- `lesson.active_interaction_session` is set to `session_after` before the directed explanation call.

Current Board AI handoff:

- Builds a copied request whose message includes a system instruction to explain only the current sequence unit and not advance.
- Calls `_generate_board_directed_explanation_message(...)`.
- Passes `requirements.model_copy(update={"target_location": focus, "location_status": "resolved"})`.
- Passes `target_excerpt=focus_context(focus)`.
- Passes `interaction_context=interaction_context_payload(session=session_after)`.

Current interaction decision:

```text
route="continue_rule"
reason="用户追问当前{unit_label}，继续围绕当前{unit_label}讲解。"
progress_note=session_after.progress_note
user_intent="追问当前{unit_label}"
```

Current commit metadata:

- `kind="interaction_flow"`
- unchanged `user_message`
- generated `assistant_message`
- generated `assistant_message_source`
- `board_explanation_directive`
- `_task_metadata(..., focus=focus, requirement_cleared=False)`
- `interaction_session_metadata(before=session_before, after=session_after, decision=decision)`

Current commit:

- label: `Section explanation follow-up`
- message: `Answered a follow-up within the current sequential section`
- board document unchanged

Current response:

- `BoardDecision(action="no_change", reason=decision.reason)`
- `interaction_decision=decision`
- `resolved_focus=focus`

Current exact trace:

```text
CONTEXT_LOAD
-> TURN_CONTEXT_BUILD
-> BOARD_ACTION_DECIDE
-> CHAT_TURN_GATE
-> RESOURCE_PREFLIGHT
-> ACTIVE_INTERACTION_CHECK decision="handled"
-> INTERACTION_SEQUENCE_CHECK decision="follow_up_current"
-> INTERACTION_CONTINUE decision="continue_rule"
-> PERSIST_CHAT_COMMIT decision="committed"
-> RESPONSE_ASSEMBLE decision="assembled"
```

Failure ordering currently expected by tests:

- If the directed explanation generator fails, only the prefix through `INTERACTION_SEQUENCE_CHECK decision="follow_up_current"` may be present.
- If commit/save fails, `INTERACTION_CONTINUE` may be present but not `PERSIST_CHAT_COMMIT` or `RESPONSE_ASSEMBLE`.
- If response assembly fails, `INTERACTION_CONTINUE` and `PERSIST_CHAT_COMMIT` may be present but not `RESPONSE_ASSEMBLE`.

## Decision: `advance`

Current trigger:

- Active sequence session as above.
- User message is a sequence continue message.
- `next_index = session_before.sequence_index + 1`
- `next_index < len(session_before.sequence_items)`

Current state transition:

- `focus = session_before.sequence_items[next_index]`
- `sequence_index` becomes `next_index`.
- `target_focus` becomes `focus`.
- `reference_context` becomes `focus_context(focus)`.
- `progress_note` becomes `准备讲解第 {next_index + 1}/{len(sequence_items)} 个{unit_label}。`
- `turn_count` increments by 1.
- `status` becomes `"active"`.
- `pause_reason` becomes `""`.
- `lesson.active_interaction_session` is set to `session_after` before the directed explanation call.

Current Board AI handoff:

- Builds a copied request with `_section_sequence_instruction(...)`.
- Calls `_generate_board_directed_explanation_message(...)`.
- Passes `requirements.model_copy(update={"target_location": focus, "location_status": "resolved"})`.
- Passes `target_excerpt=focus_context(focus)`.
- Passes `interaction_context=interaction_context_payload(session=session_after)`.

Current interaction decision:

```text
route="continue_rule"
reason="用户确认当前{unit_label}后继续下一个{unit_label}。"
progress_note=session_after.progress_note
user_intent="继续顺序讲解"
```

Current commit metadata:

- `kind="interaction_flow"`
- unchanged `user_message`
- generated `assistant_message`
- generated `assistant_message_source`
- `board_explanation_directive`
- `_task_metadata(..., focus=focus, requirement_cleared=False)`
- `interaction_session_metadata(before=session_before, after=session_after, decision=decision)`

Current commit:

- label: `Section explanation turn`
- message: `Continued a sequential section explanation session`
- board document unchanged

Current response:

- `BoardDecision(action="no_change", reason=decision.reason)`
- `interaction_decision=decision`
- `resolved_focus=focus`

Current exact trace:

```text
CONTEXT_LOAD
-> TURN_CONTEXT_BUILD
-> BOARD_ACTION_DECIDE
-> CHAT_TURN_GATE
-> RESOURCE_PREFLIGHT
-> ACTIVE_INTERACTION_CHECK decision="handled"
-> INTERACTION_SEQUENCE_CHECK decision="advance"
-> INTERACTION_CONTINUE decision="continue_rule"
-> PERSIST_CHAT_COMMIT decision="committed"
-> RESPONSE_ASSEMBLE decision="assembled"
```

Failure ordering currently expected by tests:

- If the directed explanation generator fails, only the prefix through `INTERACTION_SEQUENCE_CHECK decision="advance"` may be present.
- If commit/save fails, `INTERACTION_CONTINUE` may be present but not `PERSIST_CHAT_COMMIT` or `RESPONSE_ASSEMBLE`.
- If response assembly fails, `INTERACTION_CONTINUE` and `PERSIST_CHAT_COMMIT` may be present but not `RESPONSE_ASSEMBLE`.

## Trace Before / After Summary

`trace_before` for both owned decisions:

```text
CONTEXT_LOAD
-> TURN_CONTEXT_BUILD
-> BOARD_ACTION_DECIDE
-> CHAT_TURN_GATE
-> RESOURCE_PREFLIGHT
-> ACTIVE_INTERACTION_CHECK
```

`trace_after.follow_up_current`:

```text
INTERACTION_SEQUENCE_CHECK decision="follow_up_current"
-> INTERACTION_CONTINUE decision="continue_rule"
-> PERSIST_CHAT_COMMIT decision="committed"
-> RESPONSE_ASSEMBLE decision="assembled"
```

`trace_after.advance`:

```text
INTERACTION_SEQUENCE_CHECK decision="advance"
-> INTERACTION_CONTINUE decision="continue_rule"
-> PERSIST_CHAT_COMMIT decision="committed"
-> RESPONSE_ASSEMBLE decision="assembled"
```

## State Invariants

- `board_document` remains unchanged for both owned decisions.
- `active_interaction_session` remains active for both owned decisions.
- `follow_up_current` must not increment `sequence_index`.
- `advance` must increment `sequence_index` exactly once.
- Both paths must increment `turn_count` exactly once.
- Both paths must set `target_focus` and `reference_context` to the focus being explained.
- Both paths must clear `pause_reason`.
- Both paths must keep `status="active"`.
- Both paths must not call generic `decide_interaction_turn(...)`.
- Both paths must not call `handle_active_interaction_turn(...)`.
- Both paths must not call `handle_active_interaction_exit(...)`.
- Both paths must not call `attempt_interaction_board_task_handoff(...)`.
- Both paths must not call `handle_interaction_handoff_fallback(...)`.
- Both paths must use `BoardExplanationDirective` handoff via `_generate_board_directed_explanation_message(...)`; Chatbot must not free-answer outside the board directive.

## History Invariants

- A chat commit is created only after successful directed explanation generation.
- Commit metadata stays `kind="interaction_flow"`.
- Commit metadata includes unchanged user message, assistant message, assistant source, board explanation directive, task metadata, and interaction session metadata.
- No `LearningRequirement` version or event is created.
- No `BoardTask` version, event, or consume is created.
- No board commit is created.
- Trace fields must not leak into response payloads, SSE final payloads, commit metadata, `InteractionSession` metadata, requirement history, or BoardTask history.
- `PERSIST_CHAT_COMMIT` must include the created commit id only after commit and save succeed as current tests expect.

## Candidate New Files

After #79 resolves, candidate handler file:

- `apps/api/app/services/chat/paths/interaction_sequence_continue.py`

Candidate focused test file:

- `apps/api/tests/board_task/test_interaction_sequence_continue.py`

Expected existing files touched only after #79 resolves:

- `apps/api/app/services/chatbot.py`
- `apps/api/tests/board_task/test_interaction_sequence_trace.py`

Files that should remain untouched for this extraction:

- `apps/api/app/services/workflow_trace.py`
- `apps/api/app/models.py`
- `apps/api/app/routers/chat.py`
- `apps/api/app/services/chat_service.py`
- shared test helpers outside the lane-owned sequence tests

## Commit A After #79 Resolves

Recommended Commit A:

Add `interaction_sequence_continue.py` and focused handler tests without changing production dispatch yet.

Shape:

- Define `SequenceContinueOutcome = Literal["follow_up_current", "advance"]`.
- Add an explicit outcome payload/dataclass so the caller still owns message classification, `next_index`, `unit_label`, and `INTERACTION_SEQUENCE_CHECK` recording.
- The handler owns only validation, `session_after` mutation, copied request construction, directed explanation generation, `InteractionTurnDecision` construction, commit metadata, normalize/save, `INTERACTION_CONTINUE`, `PERSIST_CHAT_COMMIT`, `RESPONSE_ASSEMBLE`, and response building.
- Tests should cover `follow_up_current`, `advance`, validation failures, generator/commit/save/response failure ordering, no trace leak, board unchanged, and no LearningRequirement/BoardTask history writes.

Why this should be separate:

- It lets the new handler prove parity against current behavior before `chatbot.py` call-site edits.
- It avoids fighting PR #79's same-function edits while #79 is unresolved.

## Commit B After #79 Resolves

Recommended Commit B:

Wire `_handle_section_explanation_sequence_turn(...)` to call the new continuation handler for `follow_up_current` and `advance`.

Shape:

- Keep `_handle_section_explanation_sequence_turn(...)` as the sequence detection and routing owner.
- Keep caller-side `INTERACTION_SEQUENCE_CHECK decision="follow_up_current"` and `decision="advance"` exactly where the current behavior records them.
- Do not move `exit_requested` or `completed` if #79 already owns them through `interaction_sequence_end.py`.
- Update `test_interaction_sequence_trace.py` integration assertions so exact traces and metadata remain byte-for-byte equivalent, except for implementation location.
- Keep existing guardrails proving generic active interaction, new-task handoff, empty-decision, and fallback handlers are not called for handled sequence continuation turns.

## Expected Conflicts With #79

Expected conflict files:

- `apps/api/app/services/chatbot.py`
- `apps/api/tests/board_task/test_interaction_sequence_trace.py`

Expected non-conflict new files:

- `apps/api/app/services/chat/paths/interaction_sequence_continue.py`
- `apps/api/tests/board_task/test_interaction_sequence_continue.py`

Resolution notes:

- Rebase after #79 merges so `interaction_sequence_end.py` and `test_interaction_sequence_end.py` are already present.
- Preserve #79's end-handler ownership for `exit_requested` and `completed`.
- Lane A should only replace the remaining inline `follow_up_current` and `advance` blocks.
- Avoid duplicate dependency protocols where #79 already introduced compatible patterns for sequence end.
- Keep caller ownership aligned with #79: classification stays in `chatbot.py`; handler owns terminal side effects.

## Focused Tests Needed

Focused tests after #79 resolves:

- `./.venv/bin/python -m pytest apps/api/tests/board_task/test_interaction_sequence_continue.py -q`
- `./.venv/bin/python -m pytest apps/api/tests/board_task/test_interaction_sequence_trace.py -q`
- `./.venv/bin/python -m pytest apps/api/tests/board_task/test_interaction_sequence_end.py apps/api/tests/board_task/test_interaction_sequence_continue.py apps/api/tests/board_task/test_interaction_sequence_trace.py -q`
- `./.venv/bin/python -m pytest apps/api/tests/board_task/test_workflow_trace.py apps/api/tests/board_task/test_interaction_empty_decision.py apps/api/tests/board_task/test_interaction_board_task_handoff.py apps/api/tests/board_task/test_interaction_handoff_fallback.py -q`

Full verification before final PR:

- `npm run verify`

## Known Risks

- Current behavior mutates `lesson.active_interaction_session` before directed explanation generation. The extraction should preserve this ordering unless a separate behavior-changing PR explicitly fixes failure rollback.
- The future handler will need a focused dependency shape that does not overfit to #79's end handler, but also does not duplicate more helper protocols than necessary.
- PR #79 may force-push again before merge; re-check its final diff before implementing Commit A.
- `test_interaction_sequence_trace.py` currently owns exact trace and integration invariants; both #79 and Lane A edit this file, so sequencing matters.

## Recommended Integration Order

1. Wait for #79 to merge into `main`, or get an explicit decision that #79 is excluded.
2. Rebase Lane A on the resolved `main`.
3. Commit A: add continuation handler and focused tests, no production call-site swap.
4. Commit B: wire `chatbot.py` continuation branches to the handler and update exact trace integration tests.
5. Run focused sequence/end/interaction tests.
6. Run `npm run verify`.
7. Open or update the PR with explicit parity notes: behavior change is `false`.

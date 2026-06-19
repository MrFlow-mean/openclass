# BoardTask Write Current-Main Audit

Lane: F

Branch: `codex/parallel/board-task-write-current-main-audit`

Current-main base: `cb004788511748a01dd8e76604425616b8f012f6`

Historical PR evidence: PR #77 local ref `origin/pr/77` at `c9e08bc49949412a2171bcf9ba943420606d3465`; merge base with current main is `471e36287efec9f4928dce9a5cbd83fcff6456e4`.

## Scope Classification

需求：BoardTask write current-main audit.

要改的文件：only this audit report.

问题属于：

- [x] 通用产品能力
- [x] 链路状态
- [x] schema / 数据结构问题
- [x] 测试缺口
- [ ] 内容形态抽象
- [ ] prompt 质量问题
- [ ] UI 交互问题
- [ ] 资料解析问题
- [ ] 特定教材 adapter
- [ ] demo / 测试样例

是否引入以下内容：

- [ ] 学科关键词
- [ ] 教材关键词
- [ ] 固定 HTML
- [ ] 固定讲义内容
- [ ] demo 内容
- [ ] 针对单一测试样例的分支

Verdict: this audit stays generic. It does not propose subject-specific, textbook-specific, or demo-specific routing.

## Evidence Read

- `AGENTS.md`: generic workflow, role boundaries, BoardTask write requirements, history and trace constraints.
- `docs/architecture/chat-workflow-graph.md`: target node model and existing-board write path specification.
- `apps/api/app/services/chatbot.py`: current `_handle_existing_board_task_flow` and `_execute_board_task_write`.
- `apps/api/app/services/workflow_trace.py`: available `NodeId` values and collector behavior.
- `apps/api/app/services/board_task_history.py`: run/version/event persistence semantics.
- Existing tests in `apps/api/tests/test_ai_logging.py` and `apps/api/tests/board_task/test_workflow_trace.py`.
- PR #77 as historical evidence only: `git fetch origin refs/pull/77/head:refs/remotes/origin/pr/77`, `git diff origin/main...origin/pr/77`, and `git show origin/pr/77:apps/api/app/services/chat/paths/board_task_write.py`.

`gh pr view 77` was unavailable because the local GitHub CLI is not authenticated, so the audit used local Git evidence.

## Current-Main Behavior

The current-main BoardTask write path is still inline in `chatbot.py`.

### Content-Absent Write Confirmation

- If an active board task is `requested_action == "write"` and `confirmation_status == "awaiting"`, `_handle_existing_board_task_flow` handles decline or confirmation before recalculating the task.
- Decline calls `board_task_history.not_executed`, clears `lesson.board_task_requirements`, commits a chat-flow cancellation, and saves.
- Confirmation clones the existing task, sets `confirmation_status = "confirmed"` and `progress = 100`, then calls `_execute_board_task_write` without a `route_decision`.
- Because `route_decision` is absent on this confirmation path, write metadata records `board_task_decision = None` and uses an implicit board-search evidence fallback.

### Normal Write Route

- New write requests first go through `update_board_task_from_chat`, `_activate_board_task_requirements`, and `board_task_history.record_update`.
- Incomplete tasks return a clarification and do not write the board.
- Ready tasks resolve target focus when required, persist resolved task updates, then choose a route via local fallback or `openai_course_ai.generate_board_task_route_decision`.
- Missing required focus is converted back to `clarify_location`; autonomous location choice can turn same-scope ambiguous write candidates into a focused write when the user grants that autonomy.
- `decision.route == "await_write_confirmation"` records an awaiting-confirmation board-task version, commits a no-document-change confirmation prompt, and does not call BoardEditor.
- `decision.route == "write"` delegates to `_execute_board_task_write` with route decision, action decision, search evidence, and source interaction metadata.

### Write Execution

`_execute_board_task_write` currently:

- Builds `task_requirements` from the BoardTask sheet.
- Uses `expand_target` when there is a target focus and `append_section` otherwise.
- Uses `route_decision.write_proposal` when present, otherwise `board_task.question_or_topic`.
- Records the board task as `ready` or `awaiting_confirmation` before editor execution.
- Calls `edit_existing_document` with `selection_excerpt=None`, `target_scope` from the route decision or append/focus fallback, and `allow_replace_document=False`.
- On changed document, refreshes lesson runtime, rebuilds the teaching guide, and either accepts the BoardEditor message or requests a board-directed explanation.
- On failure, records `board_task_history.execution_failed`, does not consume the run, does not clear the active task, and returns failure operation status.
- On success, creates a `Board task write` lesson commit with board patch metadata, decision trace metadata, board-search evidence, BoardTask metadata, and task metadata.
- After the success commit, consumes the BoardTask run, clears active board task and first-level task requirements, saves, and returns a response with the completed board task sheet.

## PR #77 Comparison

PR #77 is historical extraction evidence, not implementation source for this lane.

Observed PR #77 shape:

- Added `apps/api/app/services/chat/paths/board_task_write.py`.
- Moved the body of current `_execute_board_task_write` into `handle_board_task_write`.
- Added a dependency object (`BoardTaskWriteDependencies`) to inject local chatbot helpers.
- Replaced the two write call sites in `chatbot.py` with `handle_board_task_write`.
- Added spy-style tests proving only write routes call the new handler and non-write routes do not.

Conceptual parity with current main:

- The extracted handler body matches the current inline behavior closely.
- The confirmation path still passes no route decision.
- The targeted write path still passes route decision and board-search evidence.
- Failure and success semantics are unchanged: failure records `execution_failed`; success commits, consumes, and clears.

Why PR #77 should not be replayed directly:

- It is based on older branch state (`471e362...`) and predates the latest current-main trace work at `cb004788...`.
- It does not add write-specific workflow trace nodes.
- Reapplying it now would be a handler extraction, which this lane was explicitly told not to do.
- Its tests are useful as a future extraction contract, but they assume a handler symbol that current main intentionally does not have.

## Trace Audit

### Trace Before

Current main has `NodeId` constants for BoardTask write nodes:

- `BOARD_TASK_COLLECT`
- `BOARD_TASK_READY_PERSIST`
- `BOARD_TARGET_RESOLVE`
- `BOARD_ROUTE_DECIDE`
- `BOARD_AWAIT_WRITE_CONFIRMATION`
- `BOARD_WRITE_CONFIRMATION_HANDLE`
- `BOARD_WRITE_EXECUTE`
- `BOARD_TASK_FAILURE`
- `PERSIST_BOARD_COMMIT`

However, `chatbot.py` currently records only the shared top-level prefix and interaction/session paths. There are no current `record_workflow_step` calls for the BoardTask write path. A current BoardTask write trace therefore records at most:

1. `CONTEXT_LOAD`
2. `TURN_CONTEXT_BUILD`
3. `BOARD_ACTION_DECIDE`
4. `CHAT_TURN_GATE`
5. `RESOURCE_PREFLIGHT`
6. `ACTIVE_INTERACTION_CHECK`

Then it returns through inline BoardTask write logic without specific write nodes.

### Trace After This Lane

No production trace change was made. Trace behavior is unchanged.

Recommended future trace-only candidate:

- Record `BOARD_TASK_COLLECT` after BoardTask sheet update.
- Record `BOARD_TASK_CLARIFY_FIELDS` when `progress < 100`.
- Record `BOARD_TASK_READY_PERSIST` with run/version stamp when ready.
- Record `BOARD_TARGET_RESOLVE` after target resolution or synthetic whole-document focus.
- Record `BOARD_ROUTE_DECIDE` after route decision and route-scope normalization.
- Record `BOARD_AWAIT_WRITE_CONFIRMATION` when content is absent and the user must confirm.
- Record `BOARD_WRITE_CONFIRMATION_HANDLE` for both confirm and decline of an awaiting write task.
- Record `BOARD_WRITE_EXECUTE` after editor execution succeeds far enough to produce a changed document.
- Record `BOARD_TASK_FAILURE` on unchanged/unsafe write output.
- Record `PERSIST_BOARD_COMMIT` only after the success commit exists and the workspace save path is past the failure point.
- Record `RESPONSE_ASSEMBLE` only when the response is about to be returned, matching existing trace discipline.

This should be a trace-only PR with parity tests that assert traced and untraced visible responses and commit metadata remain equal.

## State Invariants

Current main preserves these write-path state invariants:

- Blank-board requests do not enter `_handle_existing_board_task_flow`.
- Existing-board writes are routed through `BoardTaskRequirementSheet`, not first-level `LearningRequirementSheet`.
- Incomplete BoardTask sheets clarify rather than write.
- Content-absent write asks for confirmation before BoardEditor execution.
- Targeted write requires focus unless route semantics explicitly allow append/content-absent behavior.
- Same-section autonomous location choice can choose a target; cross-section ambiguity stays a clarification.
- BoardEditor receives structured task requirements and target focus/scope, not raw authority to rewrite unrelated board content.
- Success clears the active BoardTask and first-level task requirements.
- Failure leaves the BoardTask active/retryable.

## History Invariants

Current main preserves these history invariants:

- BoardTask updates are persisted through `BoardTaskHistoryRecorder.record_update`.
- Awaiting write confirmation is persisted with status `awaiting_confirmation`.
- Declined writes are finished with `not_executed`.
- Failed writes record an `execution_failed` event and keep the active status.
- Successful writes create a `Board task write` lesson commit.
- Successful write commit metadata includes `board_task_run_id`, `board_task_version_id`, `board_task_route = "write"`, `board_task_cleared = true`, `active_requirement_sheet_after = null`, board patch metadata when available, and board-search evidence.
- Successful writes call `board_task_history.consume(commit_id=...)` after the commit exists.

One known metadata limitation is intentional current behavior: the confirmation path has no route decision, so `board_task_decision` is `None` on the actual write commit after user confirmation.

## Existing Focused Test Coverage

Current tests already cover:

- Missing content waits for write confirmation, then writes and consumes the BoardTask run.
- Targeted write uses found location without confirmation and records found board-search evidence.
- Structured patch operations are committed for existing-board write.
- Existing-board write does not update first-level learning requirements.
- Recent write focus can be inherited for direct follow-up expansion.
- Autonomous write location choice uses a same-section tail candidate.
- Autonomous write location choice does not cross sections and records `execution_failed` instead of committing an unsafe write.
- Repeated missing edit can archive the old edit task and open a write confirmation task.
- Append/follow-up write paths stay in board editing rather than requirement update.

Current trace tests cover ordinary chat, resource prompts, and interaction paths, but not BoardTask write terminals.

## Tests Added In This Lane

No tests were added.

Reason: the obvious current-main gap is write-specific workflow trace coverage, but adding a failing trace expectation without the production trace calls would not be a safe focused test. Adding PR #77 spy tests would require introducing a handler symbol, which is outside this lane's scope.

## Proposed Future Extraction Contract

A future current-main BoardTask write PR should be allowed only after a trace-only PR lands.

Suggested contract:

1. Extract only `_execute_board_task_write` into a current-main path module.
2. Do not change routing order in `_handle_existing_board_task_flow`.
3. Keep both call sites:
   - awaiting-confirmation confirmation call with `route_decision=None`
   - normal `decision.route == "write"` call with route decision/search evidence
4. Keep failure semantics:
   - no lesson commit on unchanged/unsafe editor output
   - `execution_failed` board-task event
   - active task remains retryable
5. Keep success semantics:
   - refresh runtime and teaching guide
   - commit `Board task write`
   - consume BoardTask run after commit
   - clear active BoardTask and first-level task requirements
6. Keep metadata parity:
   - `board_task_route`
   - `board_task_decision`
   - `board_task_cleared`
   - `board_search_evidence`
   - `target_scope`
   - board patch metadata
   - decision trace metadata
   - completed task/requirement cleanup metadata
7. Keep no-leak trace contract:
   - workflow trace must remain in-memory/test-only unless a separate API contract explicitly changes it.
8. Add extraction tests that spy on the handler boundary:
   - confirmed awaiting-write calls handler once with confirmed task and `route_decision=None`
   - targeted write calls handler once with `route == "write"`
   - explain/edit/chat/clarify/await-confirmation prompt paths do not call write handler
9. Add parity tests:
   - traced and untraced write responses have equal visible response and commit metadata
   - extracted and inline-equivalent behavior is proven through existing write tests

Do not extract write and edit together. Their focus, target-scope, failure, and confirmation semantics differ.

## Recommended Integration Order

1. Merge any current-main trace-only BoardTask collect/clarify work first.
2. Add BoardTask write trace instrumentation with parity tests.
3. Add current-main handler-boundary tests inspired by PR #77, without using PR #77 as source.
4. Extract BoardTask write handler in a narrow PR after trace coverage is stable.
5. Extract BoardTask edit separately.

## Known Risks

- The current confirmation write path has weaker route-decision metadata than normal targeted writes.
- Adding trace calls around persistence must avoid recording `PERSIST_BOARD_COMMIT` or `RESPONSE_ASSEMBLE` before `_save_workspace_for_user` succeeds.
- A direct PR #77 replay would likely conflict with current-main trace/extraction stack and would not satisfy this lane's no-extraction scope.
- The current write function is still in `chatbot.py`, so any future modifications can continue to grow the file unless extraction is planned after trace coverage.

## Verification Commands

Focused verification run from this worktree:

```bash
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/test_ai_logging.py -k "existing_board_missing_content_waits_for_write_confirmation_then_writes_and_explains or existing_board_targeted_write_uses_found_location_without_confirmation or existing_board_write_dialogue_sample_uses_board_task_not_learning_requirement or autonomous_write_location_choice" -q
/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_workflow_trace.py -q
```

Results:

- `5 passed, 122 deselected`
- `31 passed`

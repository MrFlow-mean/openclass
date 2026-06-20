# Wave 6 Preparation: Non-Transactional Windows Audit

Base: `80e246ade787e0342a39d3ce603f1208663e5ab0`

Branch: `codex/prep/nontransactional-windows-audit-wave6`

Scope: audit `commit`（提交）, `consume`（消费运行状态）, and `save`（保存落库） windows for retry（重试） and partial failure（部分失败） risk. This is preparation-only evidence; no production code was changed.

## Pre-Change Self-Check

需求：审计非事务性的 commit / consume / save 窗口，并输出风险报告。

要改的文件：`docs/maintenance/nontransactional-windows-audit-wave6.md`

问题属于：

- [x] 通用产品能力
- [ ] 内容形态抽象
- [ ] prompt 质量问题
- [ ] schema / 数据结构问题
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

结论：这是通用状态一致性审计，不是特例处理；没有新增领域硬编码。

## Existing Durability Shape

The current storage layer has an important protection: `SqliteCourseStore.save_for_user_with_histories(...)` wraps workspace replacement and history operations inside one SQLite `transaction`（事务） via `with conn:`. In that save call, `_replace_workspace(...)` rewrites the workspace tables, then requirement and board-task history operations are applied before the transaction commits. See:

- `apps/api/app/services/course_store.py:113`
- `apps/api/app/services/course_store.py:123`
- `apps/api/app/services/course_store.py:124`
- `apps/api/app/services/course_store.py:125`
- `apps/api/app/services/course_store.py:130`

Lesson commits are still created first in memory. `commit_operations(...)` appends a `CommitRecord`（提交记录）, moves the branch head, and updates the lesson timestamp before any database save happens. See `apps/api/app/services/history.py:49`, `apps/api/app/services/history.py:74`, `apps/api/app/services/history.py:75`, and `apps/api/app/services/history.py:76`.

Requirement and board-task history recorders also stage operations in memory first. `LearningRequirementHistoryRecorder.consume(...)` appends a consumed run update and event, then mutates its snapshot status to `consumed`. `BoardTaskHistoryRecorder.consume(...)` delegates to `_finish(...)`, which appends an update/event and mutates its snapshot. See:

- `apps/api/app/services/learning_requirement_history.py:497`
- `apps/api/app/services/learning_requirement_history.py:503`
- `apps/api/app/services/learning_requirement_history.py:512`
- `apps/api/app/services/learning_requirement_history.py:520`
- `apps/api/app/services/board_task_history.py:388`
- `apps/api/app/services/board_task_history.py:446`
- `apps/api/app/services/board_task_history.py:456`
- `apps/api/app/services/board_task_history.py:464`

So the database write is atomic at the final save boundary, but the business flow is not one transaction from AI generation through response delivery. The windows below are the remaining risk surface.

## Prioritized Risk List

### P1: Durable Save Before Response Delivery Can Cause Duplicate Retries

Pattern: handlers often save the committed/consumed state, then build or stream the final `ChatResponse`（聊天响应）. If response construction, serialization, HTTP delivery, or SSE（服务器发送事件） final delivery fails after the save, the durable state has advanced but the client may retry the same user turn.

Representative paths:

- Board edit: commit, consume, clear active board task, save, then response. See `apps/api/app/services/chatbot.py:2667`, `apps/api/app/services/chatbot.py:2668`, `apps/api/app/services/chatbot.py:2669`, `apps/api/app/services/chatbot.py:2672`, and `apps/api/app/services/chatbot.py:2685`.
- Board explain: commit, consume, save, then response. See `apps/api/app/services/chatbot.py:2777`, `apps/api/app/services/chatbot.py:2820`, `apps/api/app/services/chatbot.py:2825`, and `apps/api/app/services/chatbot.py:2839`.
- Initial generation: commit, consume, clear, save, then response. See `apps/api/app/services/chatbot.py:4033`, `apps/api/app/services/chatbot.py:4064`, `apps/api/app/services/chatbot.py:4069`, `apps/api/app/services/chatbot.py:4071`, and `apps/api/app/services/chatbot.py:4076`.
- Interaction start: commit, consume source board task, save, then response. See `apps/api/app/services/chat/paths/interaction_start_success.py:157`, `apps/api/app/services/chat/paths/interaction_start_success.py:196`, `apps/api/app/services/chat/paths/interaction_start_success.py:202`, and `apps/api/app/services/chat/paths/interaction_start_success.py:216`.
- Sequence explanation start: commit, consume, save, then response. See `apps/api/app/services/chat/paths/interaction_sequence_start.py:235`, `apps/api/app/services/chat/paths/interaction_sequence_start.py:287`, `apps/api/app/services/chat/paths/interaction_sequence_start.py:289`, and `apps/api/app/services/chat/paths/interaction_sequence_start.py:302`.

Existing tests intentionally encode this contract for several paths. For example, board explain response failure keeps the durable consumed event but does not record `RESPONSE_ASSEMBLE`; requirement chat has the same durable-before-response behavior. See `apps/api/tests/board_task/test_board_task_explain_trace.py:246`, `apps/api/tests/board_task/test_board_task_explain_trace.py:273`, `apps/api/tests/board_task/test_requirement_chat_path.py:205`, and `apps/api/tests/board_task/test_requirement_chat_path.py:221`.

Retry risk: `ChatRequest` has no `request_id`（请求标识） or idempotency key（幂等键）. See `apps/api/app/models.py:879`. A repeated POST can be processed as a new turn after the first turn already consumed a run or advanced the branch head. The stream route logs disconnect/no-final, but it does not reconcile a retry with the produced commit. See `apps/api/app/routers/chat.py:150`, `apps/api/app/routers/chat.py:160`, and `apps/api/app/routers/chat.py:164`.

Likely impact:

- Duplicate chat commits after client timeout or final-event delivery failure.
- Duplicate board edits if the retry enters a different route after the first run was consumed.
- User-visible mismatch: left chat says the request failed, but the board/history has advanced.

Suggested future mitigation:

- Add a per-turn idempotency key to `ChatRequest` and persist a turn outcome table keyed by user, lesson, and key.
- On retry, return the existing response or at least the existing commit/run outcome instead of re-executing AI and document mutation.
- Include the durable commit id in error/log paths when save already succeeded but response delivery failed.

### P1: Save Failure Leaves In-Memory Objects Mutated Until the Request Aborts

Pattern: `commit_operations(...)`, `consume(...)`, active sheet clearing, and session updates happen before `_save_workspace_for_user(...)`. If the save raises, SQLite usually remains unchanged, but the current in-memory `workspace` / `lesson` object is already in a success-shaped state.

Representative examples:

- Board edit mutates commit, consumes board task, clears `lesson.board_task_requirements`, then saves. See `apps/api/app/services/chatbot.py:2616`, `apps/api/app/services/chatbot.py:2667`, `apps/api/app/services/chatbot.py:2668`, `apps/api/app/services/chatbot.py:2669`, and `apps/api/app/services/chatbot.py:2672`.
- Active interaction turn commits and saves after changing `lesson.active_interaction_session`. See `apps/api/app/services/chat/paths/active_interaction_turn.py:130`, `apps/api/app/services/chat/paths/active_interaction_turn.py:152`, and `apps/api/app/services/chat/paths/active_interaction_turn.py:153`.
- Active interaction exit requires the session to be cleared before the handler, then commits and saves. See `apps/api/app/services/chat/paths/active_interaction_exit.py:107`, `apps/api/app/services/chat/paths/active_interaction_exit.py:124`, and `apps/api/app/services/chat/paths/active_interaction_exit.py:147`.

Existing tests verify the trace boundary rather than rollback. For interaction start, a consume failure leaves an extra in-memory commit but avoids save/response. See `apps/api/tests/board_task/test_interaction_start_success.py:271`, `apps/api/tests/board_task/test_interaction_start_success.py:299`, and `apps/api/tests/board_task/test_interaction_start_success.py:304`. Sequence start tests likewise expect no durable board-task events when consume/save fails. See `apps/api/tests/board_task/test_interaction_sequence_start.py:327`, `apps/api/tests/board_task/test_interaction_sequence_start.py:379`, and `apps/api/tests/board_task/test_interaction_sequence_start.py:392`.

Likely impact today is limited because request handlers normally abort and future turns reload from SQLite. The risk grows if a future orchestrator catches these exceptions and keeps working with the same mutated object, or if streaming deltas have already shown generated content before save fails.

Suggested future mitigation:

- Introduce a small unit-of-work helper that can snapshot/restore in-memory lesson fields around commit/consume/save.
- For high-risk mutation paths, stage `CommitRecord` and consume operations as values, then apply them only inside a save boundary or a single durable command.
- Add tests that assert the reloaded store and the in-memory object are either both unchanged on save failure or the dirty object is discarded.

### P2: Workflow Trace Can Record Pre-Save Success-Looking Nodes

Some trace nodes are recorded after in-memory commit creation but before the durable save. In sequence start, `BOARD_SEQUENCE_START` is recorded after the in-memory commit and before consume/save. See `apps/api/app/services/chat/paths/interaction_sequence_start.py:278`, `apps/api/app/services/chat/paths/interaction_sequence_start.py:279`, `apps/api/app/services/chat/paths/interaction_sequence_start.py:287`, and `apps/api/app/services/chat/paths/interaction_sequence_start.py:289`.

Tests explicitly expect `BOARD_SEQUENCE_START` to exist even when consume/save fails, while `PERSIST_CHAT_COMMIT` and `RESPONSE_ASSEMBLE` are absent. See `apps/api/tests/board_task/test_interaction_sequence_start.py:376`, `apps/api/tests/board_task/test_interaction_sequence_start.py:379`, and `apps/api/tests/board_task/test_interaction_sequence_start.py:382`.

Likely impact:

- Logs can contain a commit id that never became durable.
- Triage may read `BOARD_SEQUENCE_START` as durable session start unless it also checks for the later persist node.

Suggested future mitigation:

- Mark pre-save trace nodes with `durability="in_memory"` or similar.
- Reserve `commit_id` on pre-save nodes for a field name like `proposed_commit_id`.
- Add a trace invariant: any node named `PERSIST_*` must occur only after durable save succeeds.

### P2: Filesystem Side Effects Are Outside SQLite Transactions

Resource and document import paths write or delete files outside the workspace transaction.

Examples:

- Resource upload writes to `UPLOAD_DIR`, parses the file, appends the resource, then saves workspace. Parse failure unlinks the uploaded file, but save failure does not. See `apps/api/app/routers/workspace.py:207`, `apps/api/app/routers/workspace.py:215`, `apps/api/app/routers/workspace.py:217`, `apps/api/app/routers/workspace.py:220`, `apps/api/app/routers/workspace.py:224`, and `apps/api/app/routers/workspace.py:226`.
- DOCX import writes an uploaded file, imports it into the board, commits a snapshot, then saves. There is no cleanup on import or save failure in the shown path. See `apps/api/app/routers/documents.py:271`, `apps/api/app/routers/documents.py:278`, `apps/api/app/routers/documents.py:280`, `apps/api/app/routers/documents.py:281`, and `apps/api/app/routers/documents.py:288`.
- Lesson deletion saves the DB state, then deletes removed resource files. If file deletion fails, the DB has already forgotten the resources. See `apps/api/app/routers/workspace.py:181`, `apps/api/app/routers/workspace.py:182`, `apps/api/app/routers/workspace.py:183`, `apps/api/app/routers/workspace.py:184`, and `apps/api/app/services/resource_service.py:31`.

Likely impact:

- Orphan uploaded files after save failure.
- Orphan parser artifacts if parser output is created before a failed save.
- DB-to-filesystem drift after deletion failures.

Suggested future mitigation:

- Save uploads through a staged temp path, then atomically promote after DB save succeeds.
- On save failure, best-effort unlink newly written files/artifacts.
- Track resource file garbage collection as a maintenance job keyed by DB `source_path`.

### P3: Failure Events Are Retry-Friendly But Can Be Lost On Save Failure

The frozen requirement retry path is good: `generation_failed(...)` appends an event without consuming the run, so a later retry can still consume the same frozen requirement. See `apps/api/app/services/learning_requirement_history.py:523`, `apps/api/app/services/learning_requirement_history.py:531`, and `apps/api/app/services/learning_requirement_history.py:539`. The retry behavior is covered by `test_generation_retry_success_consumes_frozen_requirement`. See `apps/api/tests/test_learning_requirement_history.py:420`, `apps/api/tests/test_learning_requirement_history.py:462`, and `apps/api/tests/test_learning_requirement_history.py:465`.

Residual risk: if the failure event save itself fails, the frozen run remains retryable from the earlier checkpoint, but the failure audit event is lost. This is lower severity than duplicate mutation because it does not consume the run or corrupt the board.

Suggested future mitigation:

- Keep this as a known acceptable gap unless audit completeness becomes a product requirement.
- If needed, add best-effort AI log breadcrumbs for generation failures before the DB save.

## Checks

- `npm run test:api -- apps/api/tests/board_task/test_interaction_sequence_start.py apps/api/tests/board_task/test_board_task_explain_trace.py apps/api/tests/test_learning_requirement_history.py`
  - Result: failed before pytest because this fresh worktree does not have `.venv/bin/python`.
- `/Users/liqianhao/Desktop/openclass/.venv/bin/python -m pytest apps/api/tests/board_task/test_interaction_sequence_start.py apps/api/tests/board_task/test_board_task_explain_trace.py apps/api/tests/test_learning_requirement_history.py`
  - Result: 23 passed.
- `git diff --check`
  - Result: passed.

## Notes For Production Work

Recommended next production candidates should stay generic:

1. Add turn-level idempotency for chat mutations and interaction starts.
2. Add a reusable rollback/staging helper for in-memory lesson mutations around save failures.
3. Add staged-file cleanup for resource upload and DOCX import.
4. Clarify trace durability labels for pre-save nodes.

These are capability-level changes. They do not require subject, textbook, exam, or demo-specific logic.

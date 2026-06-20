# Wave 5 Compatibility Cleanup Inventory

Preparation-only Worker K report.

## Scope And Base

- Repository: `/Users/liqianhao/Desktop/openclass`
- Worktree（独立工作目录）: `/Users/liqianhao/Desktop/openclass-worktrees/compatibility-cleanup-wave5`
- Branch（分支）: `codex/prep/compatibility-cleanup-wave5`
- Base `MAIN_SHA`（主干提交）: `d658e679a71d6dad893e8909f8ba25080523f1bf`
- Report date: 2026-06-20
- Scope: docs-only inventory（仅文档清单） for `teaching_action`, `direct_edit`, old document actions, fallback explain, recent edit follow-up, autonomous location choice, and stale PRs（陈旧 PR）.
- Out of scope: runtime code（运行时代码）, production PR（生产 PR）, merges（合并）.

## Generality Self-Check

Requirement: inventory compatibility cleanup surfaces for the generic OpenClass chat/board workflow.

Files changed:

- `docs/maintenance/compatibility-cleanup-wave5-inventory.md`

Category:

- [x] General product capability
- [x] Content-shape / workflow-shape abstraction
- [ ] Prompt quality issue
- [x] Schema / data structure issue
- [x] UI interaction issue
- [ ] Resource parsing issue
- [ ] Specific textbook adapter
- [ ] Demo / test sample

No new domain hardcoding:

- [ ] Subject keyword
- [ ] Textbook keyword
- [ ] Fixed HTML
- [ ] Fixed lecture content
- [ ] Demo content
- [ ] Single-test special branch

This inventory is generic because it follows action shape, route（路线） shape, target-resolution state, and PR branch drift. It does not introduce subject, textbook, exam, or sample-specific behavior.

## Evidence Commands

```bash
git rev-parse HEAD
git status -sb
rg -n "teaching_action|direct_edit|DOCUMENT_WRITE_ACTIONS|fallback|recent.*edit|autonomous.*location" apps docs
curl -sS 'https://api.github.com/repos/MrFlow-mean/openclass/pulls?state=open&per_page=100'
git fetch origin pull/<PR>/head:refs/remotes/origin/pr/<PR>
git rev-list --left-right --count origin/main...origin/pr/<PR>
git cherry -v origin/main origin/pr/<PR>
git diff --name-only origin/main...origin/pr/<PR>
```

`gh`（GitHub CLI，命令行工具） is installed but not authenticated in this worktree session, so current PR state was checked through the public GitHub API（接口） and local `git` refs.

## Current Compatibility Surfaces

| Surface | Current owner | Current status | Cleanup recommendation |
|---|---|---|---|
| `teaching_action` | `ChatRequest`, `chat_turn_gate.py`, `chatbot.py`, `board_teaching.py` | Compatibility entry for section-by-section board teaching. It saves `board_explanation_directive`（板书讲解指令） but uses `board_teaching_progress`, not `BoardTaskRequirementSheet`（板书任务清单） consumption. | Keep until a BoardTask or InteractionSession（互动会话） equivalent covers continue/restart, progress, directive-approved and directive-blocked cases. Then remove API field in a separate compatibility PR. |
| `direct_edit` | `ChatRequest.interaction_mode`, `board_task_decider.py`, `chat_turn_gate.py`, legacy branch in `chatbot.py` | Still maps direct-edit mode into write/edit actions and has a legacy execution branch after canonical BoardTask has first chance. | Do not delete first. Add parity tests for selected/missing focus, operation metadata, and requirement-history non-regression; then route direct-edit through BoardTask edit/write only. |
| Old document actions | `BoardTaskAction`, `DOCUMENT_WRITE_ACTIONS`, legacy document branch in `chatbot.py` | `append_section`, `rewrite_target`, `expand_target`, `simplify_target`, plus `explain_target` still have a post-BoardTask compatibility branch. | Migrate one action at a time into canonical BoardTask write/edit/explain; keep old branch as fallback until tests cover success, focus clarification, failure, metadata, and no first-layer requirement pollution. |
| Fallback explain | Tail branch in `chatbot.py` | Existing-board explanation can still fall through to board-directed explanation without first completing a BoardTask run if earlier routes return none. It is directive-gated, but not fully BoardTask-audited. | First make `_handle_existing_board_task_flow` cover remaining explain cases. Then remove the tail fallback or convert it to an explicit BoardTask collection retry. |
| Recent edit follow-up | helper cluster in `chatbot.py` | Recent append/edit/write follow-ups inherit the latest successful board-edit focus when user says length/edit/write continuation phrases. Tests assert it stays in BoardTask route. | Preserve behavior, but move from top-level helper cluster into a target resolver policy attached to `BOARD_TARGET_RESOLVE`（板书目标定位）. |
| Autonomous location choice | helper cluster in `chatbot.py` | Guarded write-only policy: only ambiguous `clarify_location`, explicit user authorization, same heading scope, and sufficient candidate confidence. Tests cover same-section success and cross-section refusal. | Keep guarded behavior. Later move into a deterministic target-resolution policy module and trace it as resolution evidence, not a top-level compatibility route. |
| Stale PRs | GitHub open PRs | Open PR count is now 7, not the older 34 from `docs/maintenance/pr-triage.md`. | Close or refresh stale drafts separately; do not mix with runtime cleanup. |

## Evidence Details

### `teaching_action`

Evidence:

- `ChatRequest.teaching_action` remains part of the API shape at `apps/api/app/models.py:879`.
- `TeachingAction = Literal["continue", "restart"]` remains in `apps/api/app/models.py:112`.
- `decide_chat_turn` routes any non-null `teaching_action` to `existing_board_task` at `apps/api/app/services/chat_turn_gate.py:92`.
- `_handle_existing_board_task_flow` explicitly returns `None` when `request.teaching_action is not None` at `apps/api/app/services/chatbot.py:2006`.
- The legacy branch executes `teach_first_section` / `teach_next_section` at `apps/api/app/services/chatbot.py:4436`.
- `board_teaching.py` creates a board-directed explanation and only advances progress when the directive approves at `apps/api/app/services/board_teaching.py:123`.
- Tests cover continue and blocked directive behavior at `apps/api/tests/test_ai_logging.py:7180` and `apps/api/tests/test_ai_logging.py:7238`.

Cleanup read:

- This path is safe in the sense that Chatbot（左侧对话角色） is still gated by board-directed explanation.
- It is not yet canonical because there is no `board_task_run_id`, `board_task_version_id`, or BoardTask consume event.
- Recommended next step: add a focused BoardTask-compatible teaching continue spec before code movement.

### `direct_edit`

Evidence:

- `ChatInteractionMode = Literal["ask", "direct_edit"]` remains at `apps/api/app/models.py:111`.
- `board_task_decider.py` maps `direct_edit` to append/simplify/expand/rewrite at `apps/api/app/services/board_task_decider.py:53`.
- `chat_turn_gate.py` treats `interaction_mode == "direct_edit"` as an existing-board task signal at `apps/api/app/services/chat_turn_gate.py:160`.
- Canonical BoardTask gets first chance in `chatbot.py` at `apps/api/app/services/chatbot.py:4274`.
- A direct-edit legacy branch remains at `apps/api/app/services/chatbot.py:4487`.
- Gate fixtures cover existing direct-edit routing at `apps/api/tests/fixtures/chat_turn_gate_cases.json:432`.
- Decider tests cover direct-edit append priority at `apps/api/tests/board_task/test_board_task_decider.py:31`.

Cleanup read:

- The branch is a compatibility bridge for older UI/editor behavior.
- It still updates first-layer requirements before editing, which is exactly the kind of responsibility overlap Wave 5 should reduce.
- Recommended next step: do not extract this branch as-is. First add parity tests that prove BoardTask edit/write covers the direct-edit cases, then delete or thin the legacy branch.

### Old Document Actions

Evidence:

- `BoardTaskAction` still includes `generate_board`, `append_section`, `explain_target`, `rewrite_target`, `expand_target`, and `simplify_target` at `apps/api/app/models.py:69`.
- `board_task_decider.py` has `DOCUMENT_WRITE_ACTIONS` at `apps/api/app/services/board_task_decider.py:10`.
- `chatbot.py` has a separate local `DOCUMENT_WRITE_ACTIONS` for `append/rewrite/expand/simplify` at `apps/api/app/services/chatbot.py:160`.
- The legacy document action branch begins at `apps/api/app/services/chatbot.py:4648`.
- Append is handled without focus at `apps/api/app/services/chatbot.py:4691`.
- Targeted edit/explain branches resolve focus at `apps/api/app/services/chatbot.py:4759`.
- Tests now assert existing-board write goes through BoardTask metadata at `apps/api/tests/test_ai_logging.py:5671`, which means migration has already started.

Cleanup read:

- The old branch still duplicates BoardEditor（板书编辑角色） execution and focus clarification behavior.
- Do not remove all old document actions together. `append_section`, local edit, and `explain_target` have different failure and history semantics.
- Recommended order: `explain_target` fallback first, then edit actions, then append.

### Fallback Explain

Evidence:

- Architecture docs already name `LEGACY_FALLBACK_EXPLAIN` at `docs/architecture/chat-workflow-graph.md:102`.
- The final existing-board fallback branch starts with `_requests_explanation(request.message)` at `apps/api/app/services/chatbot.py:5279`.
- It uses `_generate_board_directed_explanation_message` and stores `board_explanation_directive` at `apps/api/app/services/chatbot.py:5286`.
- Canonical BoardTask explain tests exist for meaning/how-expression requests at `apps/api/tests/test_ai_logging.py:4183` and `apps/api/tests/test_ai_logging.py:4225`.
- Older targeted explanation tests still assert `task_requirement_sheet.action_type == "explain_target"` rather than BoardTask metadata at `apps/api/tests/test_ai_logging.py:3998`.

Cleanup read:

- This is directive-gated, so it does not freely teach from raw context.
- It is still a history/audit mismatch compared with canonical BoardTask explain.
- Recommended next step: add tests for the remaining fallback cases and force them through `BOARD_TASK_COLLECT -> BOARD_TARGET_RESOLVE -> BOARD_EXPLAIN_DIRECTIVE -> BOARD_EXPLAIN_COMMIT`.

### Recent Edit Follow-Up

Evidence:

- Follow-up patterns live in `chatbot.py` at `apps/api/app/services/chatbot.py:150`.
- Recent focus recovery reads the last successful board edit metadata at `apps/api/app/services/chatbot.py:539`.
- `_maybe_inherit_recent_board_edit_focus` applies the focus to edit/write tasks at `apps/api/app/services/chatbot.py:562`.
- Tests cover length follow-up and direct expand follow-up at `apps/api/tests/test_ai_logging.py:5710` and `apps/api/tests/test_ai_logging.py:5783`.

Cleanup read:

- The behavior is useful and generic: it keys off recent edit state and action shape, not subject matter.
- The current location in `chatbot.py` keeps target heuristics in the orchestrator.
- Recommended next step: move this policy to `segment_resolver.py` or a target resolver policy module only after BoardTask edit/write trace coverage is stable.

### Autonomous Location Choice

Evidence:

- Authorization phrase pattern lives at `apps/api/app/services/chatbot.py:155`.
- `_maybe_apply_autonomous_write_location_choice` only applies to write tasks with ambiguous location clarification at `apps/api/app/services/chatbot.py:683`.
- It requires same heading scope at `apps/api/app/services/chatbot.py:747`.
- It persists the chosen focus back into the BoardTask sheet at `apps/api/app/services/chatbot.py:2280`.
- Tests cover same-section auto-choice at `apps/api/tests/test_ai_logging.py:5891`.
- Tests cover cross-section refusal at `apps/api/tests/test_ai_logging.py:6002`.

Cleanup read:

- This should not be removed. It directly supports user-approved autonomous write placement.
- It should be relocated out of `chatbot.py` when target resolver policy extraction begins.
- Add trace evidence for the chosen policy so future cleanup can distinguish user-authorized autonomy from unsafe silent guessing.

## Workflow Trace Gap

`workflow_trace.py` defines compatibility-only `NodeId`（节点标识） values:

- `LEGACY_TEACHING_ACTION`
- `LEGACY_DIRECT_EDIT_ACTION`
- `LEGACY_DOCUMENT_ACTION`
- `LEGACY_FALLBACK_EXPLAIN`

Evidence: `apps/api/app/services/workflow_trace.py:62`.

However, current code search found no `record_workflow_step(NodeId.LEGACY_...)` calls. The architecture doc lists these compatibility nodes, but runtime trace does not currently mark them. This is a cleanup blocker: before deleting compatibility branches, add minimal trace points or parity tests that prove the branch is no longer entered.

## Current Open PR State

Checked via GitHub API on 2026-06-20. All 7 open PRs are draft.

| PR | Head branch | Base SHA from API | Head SHA | Drift vs `origin/main` | Files | Classification | Recommendation |
|---|---|---|---|---|---:|---|---|
| #92 `refactor: extract sequence session start path` | `codex/integrate/sequence-start-extraction` | `d658e679` | `1d5e409` | `main_only=0`, `pr_only=1`, `cherry +=1` | 3 | Active Wave 5 draft | Not stale. Keep with coordinator; do not touch from Worker K. |
| #91 `refactor: trace board task edit execution paths` | `codex/integrate/board-task-edit-trace` | `d658e679` | `62d4032` | `main_only=0`, `pr_only=1`, `cherry +=1` | 2 | Active Wave 5 draft | Not stale. Likely current BoardTask edit production candidate; do not close. |
| #80 `test: extract workflow test helpers` | `codex/extract-workflow-test-helpers` | `cb004788` | `e72c621` | `main_only=12`, `pr_only=1`, `cherry +=1` | 7 | Stale-ish test-helper draft | Recreate from current `main` if still useful. Do not merge old base. |
| #77 `Extract BoardTask write handler` | `codex/extract-board-task-write-terminal` | `471e362` | `c9e08bc` | `main_only=14`, `pr_only=1`, `cherry +=1` | 3 | Stale handler extraction | Keep as historical evidence only until write trace lands. Rebuild from current `main`; do not merge. |
| #53 `[codex] Make favicon circular` | `codex/circular-favicon` | `4a7ab01` | `95c1d1b` | `main_only=36`, `pr_only=2`, `cherry +=2` | 4 | UI preference draft | Product decision. Rebase/recreate if still wanted, otherwise close. |
| #6 `Restore book brand mark` | `codex/restore-book-logo` | `384bf58` | `ac95cb3` | `main_only=66`, `pr_only=1`, `cherry +=1` | 1 | UI preference draft | Product decision. Rebase/recreate if still wanted, otherwise close. |
| #1 `Implement realtime PM voice intake` | `codex/reset-workflow-architecture` | `300fe840` | `97bbe3a` | `main_only=177`, `pr_only=27`, `cherry -=3 +=24` | 40 | Very stale architecture experiment | Close or leave only as historical roadmap evidence. Any realtime PM work needs a new design note and fresh branch. |

Delta from the older `docs/maintenance/pr-triage.md`:

- Old audit date: 2026-06-17.
- Old open PR count: 34.
- Current open PR count: 7.
- Many stale stacked PRs have already been closed or merged elsewhere.

## Cleanup Recommendations

1. Add or verify runtime trace points for the four `LEGACY_*` nodes before deleting any compatibility branch.
2. Keep #91/#92 out of stale cleanup; they are current active Wave 5 drafts based on `d658e679`.
3. Do not merge #77. Use it only as historical evidence for a future BoardTask write extraction after current-main write trace coverage exists.
4. Migrate fallback explain next, because canonical BoardTask explain coverage is already strongest.
5. Migrate direct edit only after focused parity tests prove selection, missing focus, edit failure, and metadata behavior through BoardTask.
6. Preserve recent edit follow-up and autonomous location choice, but move both into target-resolution policy modules instead of expanding `chatbot.py`.
7. Close or refresh #80/#53/#6/#1 outside runtime cleanup; they are not blockers for compatibility branch cleanup.

## Blockers

- No authenticated `gh` session; PR audit used public GitHub API plus local git refs.
- The docs list compatibility `NodeId` values, but code does not record them yet.
- Old document-action cleanup depends on BoardTask write/edit trace coverage; deleting first would risk losing commit metadata and failure history.

## Verification

Docs-only checks:

- `git status -sb`
- `git diff --check`

Runtime tests were not required for this report and were not run.

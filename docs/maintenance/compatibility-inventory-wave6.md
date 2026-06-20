# Wave 6 Compatibility And Stale PR Inventory

Preparation-only Worker K report.

## Scope And Base

- Repository: `/Users/liqianhao/Desktop/openclass`
- Worktree（工作树）: `/Users/liqianhao/Desktop/openclass-worktrees/compatibility-inventory-wave6`
- Branch（分支）: `codex/prep/compatibility-inventory-wave6`
- Base `MAIN_SHA`（主分支提交）: `80e246ade787e0342a39d3ce603f1208663e5ab0`
- Report date: 2026-06-20
- Scope: docs-only inventory（仅文档清单） for `teaching_action`, `direct_edit`, old document actions, fallback explain, recent edit follow-up, autonomous location choice, and stale PRs（陈旧 PR）.
- Out of scope: runtime code（运行时代码）, production PR（生产 PR）, merge（合并）.

## Generality Self-Check

Requirement: refresh compatibility and stale PR inventory for the generic OpenClass chat/board workflow.

Files changed:

- `docs/maintenance/compatibility-inventory-wave6.md`
- `docs/maintenance/pr-triage.md`
- `docs/maintenance/chatbot-parallel-migration.md`

Category:

- [x] General product capability
- [x] Content/workflow shape abstraction
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

This report is generic because it inventories action shape, route（路线） shape, target-resolution state, trace coverage, and PR branch drift. It does not introduce subject, textbook, exam, or sample-specific behavior.

## Evidence Commands

```bash
git fetch origin main --prune
git ls-remote origin refs/heads/main
git rev-parse origin/main
git show -s --format='%H %ci %s' 80e246ade787e0342a39d3ce603f1208663e5ab0
rg -n "teaching_action|direct_edit|DOCUMENT_WRITE_ACTIONS|fallback|recent.*edit|autonomous.*location" apps docs
curl -sS 'https://api.github.com/repos/MrFlow-mean/openclass/pulls?state=open&per_page=100'
git fetch origin pull/<PR>/head:refs/remotes/origin/pr/<PR>
git rev-list --left-right --count 80e246ade787e0342a39d3ce603f1208663e5ab0...origin/pr/<PR>
git cherry -v 80e246ade787e0342a39d3ce603f1208663e5ab0 origin/pr/<PR>
git diff --name-only 80e246ade787e0342a39d3ce603f1208663e5ab0...origin/pr/<PR>
```

`gh`（GitHub CLI，GitHub 命令行工具） is installed but not authenticated in this session, so current PR state was checked through the public GitHub API（接口） and local `git` refs.

## Current Compatibility Surfaces

| Surface | Current owner | Current status | Cleanup recommendation |
|---|---|---|---|
| `teaching_action` | `ChatRequest`, `chat_turn_gate.py`, `chatbot.py`, `board_teaching.py` | Compatibility entry for section-by-section board teaching. It stores `board_explanation_directive`（板书讲解指令） and `teaching_progress`, but does not consume a `BoardTaskRequirementSheet`（板书任务清单） run. | Keep until a BoardTask or `InteractionSession`（互动会话） equivalent covers continue/restart, progress, directive-approved, and directive-blocked cases. Then remove the API field separately. |
| `direct_edit` | `ChatRequest.interaction_mode`, `board_task_decider.py`, `chat_turn_gate.py`, legacy branch in `chatbot.py` | Still maps direct-edit mode into append/simplify/expand/rewrite and has a legacy execution branch after canonical BoardTask has first chance. | Do not delete first. Add parity tests for selected focus, missing focus, operation metadata, and requirement-history non-regression; then route through BoardTask edit/write only. |
| Old document actions | `BoardTaskAction`, `DOCUMENT_WRITE_ACTIONS`, legacy document branch in `chatbot.py` | `append_section`, `rewrite_target`, `expand_target`, `simplify_target`, and `explain_target` still have a post-BoardTask compatibility branch. | Migrate one action at a time into canonical BoardTask write/edit/explain nodes. Keep old branch as fallback until success, focus clarification, failure, metadata, and requirement-history parity are covered. |
| Fallback explain | Tail branch in `chatbot.py` | Existing-board explanation can still fall through to board-directed explanation without a completed BoardTask run if earlier routes return none. It is directive-gated, but not fully BoardTask-audited. | First make `_handle_existing_board_task_flow` cover remaining explain cases. Then remove the tail fallback or convert it to an explicit BoardTask collection retry. |
| Recent edit follow-up | helper cluster in `chatbot.py` | Recent append/edit/write follow-ups inherit the latest successful board-edit focus when user phrasing indicates length edits or continuation. Tests assert it stays in BoardTask route. | Preserve behavior, but later move it into a target resolver policy attached to `BOARD_TARGET_RESOLVE`（板书目标定位）, not a top-level orchestrator helper. |
| Autonomous location choice | helper cluster in `chatbot.py` | Guarded write-only policy: ambiguous `clarify_location`, explicit user authorization, same heading scope, and sufficient candidate confidence. Tests cover same-section success and cross-section refusal. | Keep guarded behavior. Later move into a deterministic target-resolution policy module and trace it as resolution evidence. |
| Stale PRs | GitHub open PRs | Open PR count is now 5, not the old 34 from `docs/maintenance/pr-triage.md` and not the 7 observed by the older Wave 5 prep branch. | Refresh stale PR cleanup around the 5 current drafts only; do not reopen or merge closed stale branches. |

## Evidence Details

### `teaching_action`

Evidence:

- `TeachingAction = Literal["continue", "restart"]` remains in `apps/api/app/models.py:112`.
- `ChatRequest.teaching_action` remains in the API request shape at `apps/api/app/models.py:893`.
- `decide_chat_turn` routes any non-null `teaching_action` to `existing_board_task` at `apps/api/app/services/chat_turn_gate.py:92`.
- `_handle_existing_board_task_flow` explicitly returns `None` when `request.teaching_action is not None` at `apps/api/app/services/chatbot.py:1895`.
- The legacy branch executes `teach_first_section` / `teach_next_section` at `apps/api/app/services/chatbot.py:4359`.
- `board_teaching.py` uses a board-directed explanation and only advances progress when the directive approves at `apps/api/app/services/board_teaching.py:123`.
- Tests cover continue and directive-blocked behavior at `apps/api/tests/test_ai_logging.py:7180` and `apps/api/tests/test_ai_logging.py:7238`.

Cleanup read:

- Safe enough to preserve because Chatbot（左侧对话角色） remains board-directive gated.
- Not canonical yet because it has no `board_task_run_id`, `board_task_version_id`, or BoardTask consume event.
- Next step: add a BoardTask-compatible teaching continue spec before code movement.

### `direct_edit`

Evidence:

- `ChatInteractionMode = Literal["ask", "direct_edit"]` remains at `apps/api/app/models.py:111`.
- `board_task_decider.py` maps `direct_edit` to append/simplify/expand/rewrite at `apps/api/app/services/board_task_decider.py:53`.
- `chat_turn_gate.py` treats `interaction_mode == "direct_edit"` as an existing-board task signal at `apps/api/app/services/chat_turn_gate.py:160`.
- A direct-edit legacy branch remains at `apps/api/app/services/chatbot.py:4410`.
- Gate fixtures cover direct-edit routing at `apps/api/tests/fixtures/chat_turn_gate_cases.json:432`.
- Decider tests cover direct-edit append priority at `apps/api/tests/board_task/test_board_task_decider.py:31`.

Cleanup read:

- This is a compatibility bridge for older UI/editor behavior.
- It still overlaps with first-layer requirement update and direct BoardEditor（板书编辑角色） execution.
- Next step: add parity tests that prove BoardTask edit/write covers the direct-edit cases, then thin or delete the branch.

### Old Document Actions

Evidence:

- `BoardTaskAction` still includes `generate_board`, `append_section`, `explain_target`, `rewrite_target`, `expand_target`, and `simplify_target` in `apps/api/app/models.py`.
- `board_task_decider.py` defines `DOCUMENT_WRITE_ACTIONS` at `apps/api/app/services/board_task_decider.py:10`.
- `chatbot.py` defines a local `DOCUMENT_WRITE_ACTIONS` for append/rewrite/expand/simplify at `apps/api/app/services/chatbot.py:165`.
- The legacy document action branch begins at `apps/api/app/services/chatbot.py:4571`.
- Append is handled without focus at `apps/api/app/services/chatbot.py:4614`.
- Targeted edit/explain branches resolve focus at `apps/api/app/services/chatbot.py:4682`.
- Board action fixture tests still preserve old action names at `apps/api/tests/board_task/test_board_task_turn_fixtures.py:49`.

Cleanup read:

- The old branch duplicates BoardEditor execution and focus clarification behavior already being pulled into BoardTask routes.
- Do not remove all actions together. Append, local edit, and target explain have different failure and history semantics.
- Suggested order: fallback explain first, targeted edit actions second, append last.

### Fallback Explain

Evidence:

- Architecture docs name `LEGACY_FALLBACK_EXPLAIN` at `docs/architecture/chat-workflow-graph.md:102`.
- The final existing-board explanation fallback starts at `apps/api/app/services/chatbot.py:5202`.
- It uses `_generate_board_directed_explanation_message` and stores `board_explanation_directive` at `apps/api/app/services/chatbot.py:5209`.
- The docs map this branch to `LEGACY_FALLBACK_EXPLAIN` at `docs/architecture/chat-workflow-graph.md:738`.

Cleanup read:

- It is directive-gated, so it is not free Chatbot teaching from raw context.
- It is still a history/audit mismatch compared with canonical BoardTask explain.
- Next step: add tests for remaining fallback cases, then force them through `BOARD_TASK_COLLECT -> BOARD_TARGET_RESOLVE -> BOARD_EXPLAIN_DIRECTIVE -> BOARD_EXPLAIN_COMMIT`.

### Recent Edit Follow-Up

Evidence:

- Follow-up patterns live in `chatbot.py` at `apps/api/app/services/chatbot.py:154`.
- Recent focus recovery reads the last successful board edit metadata at `apps/api/app/services/chatbot.py:543`.
- `_maybe_inherit_recent_board_edit_focus` applies the focus to edit/write tasks at `apps/api/app/services/chatbot.py:566`.
- Tests cover length follow-up and direct expand follow-up at `apps/api/tests/test_ai_logging.py:5710` and `apps/api/tests/test_ai_logging.py:5783`.

Cleanup read:

- The behavior is useful and generic: it keys off recent edit state and action shape, not subject matter.
- The current location in `chatbot.py` keeps target heuristics in the orchestrator.
- Next step: move this policy to a target resolver policy module after BoardTask edit/write trace coverage is stable.

### Autonomous Location Choice

Evidence:

- Authorization phrase pattern lives at `apps/api/app/services/chatbot.py:159`.
- `_maybe_apply_autonomous_write_location_choice` only applies to write tasks with ambiguous location clarification at `apps/api/app/services/chatbot.py:687`.
- It persists the chosen focus back into the BoardTask sheet at `apps/api/app/services/chatbot.py:2175`.
- Tests cover same-section auto-choice at `apps/api/tests/test_ai_logging.py:5891`.
- Tests cover cross-section refusal at `apps/api/tests/test_ai_logging.py:6002`.

Cleanup read:

- This should not be removed. It supports user-approved autonomous write placement.
- It should be relocated out of `chatbot.py` when target resolver policy extraction begins.
- Add trace evidence for the chosen policy so future cleanup can distinguish user-authorized autonomy from unsafe silent guessing.

## Workflow Trace Gap

`workflow_trace.py` defines compatibility-only `NodeId`（节点标识） values:

- `LEGACY_TEACHING_ACTION`
- `LEGACY_DIRECT_EDIT_ACTION`
- `LEGACY_DOCUMENT_ACTION`
- `LEGACY_FALLBACK_EXPLAIN`

Evidence: `apps/api/app/services/workflow_trace.py:62`.

Current code search found no `record_workflow_step(NodeId.LEGACY_...)` calls. The architecture doc lists these compatibility nodes, but runtime trace does not mark them yet. This is a cleanup blocker: before deleting compatibility branches, add minimal trace points or parity tests that prove the branch is no longer entered.

## Current Open PR State

Checked via GitHub API on 2026-06-20. `git ls-remote origin refs/heads/main`, `git rev-parse origin/main`, and GitHub `commits/main` all reported `80e246ade787e0342a39d3ce603f1208663e5ab0`.

All current open PRs are draft. Drift below is measured against the required Worker K base `MAIN_SHA`, not against each PR's stale API `base.sha`.

| PR | Head branch | API base SHA | Head SHA | Drift vs `MAIN_SHA` | Files | Classification | Recommendation |
|---|---|---|---|---|---:|---|---|
| #80 `test: extract workflow test helpers` | `codex/extract-workflow-test-helpers` | `cb004788` | `e72c621` | `main_only=15`, `pr_only=1`, `cherry -/+=0/1` | 7 | Current but behind-main test helper draft | Recreate or rebase from current `main` if still useful. Do not merge stale base directly. |
| #77 `Extract BoardTask write handler` | `codex/extract-board-task-write-terminal` | `471e362` | `c9e08bc` | `main_only=17`, `pr_only=1`, `cherry -/+=0/1` | 3 | Stale handler extraction | Keep as historical evidence only until write trace lands. Rebuild from current `main`; do not merge. |
| #53 `[codex] Make favicon circular` | `codex/circular-favicon` | `4a7ab01` | `95c1d1b` | `main_only=39`, `pr_only=2`, `cherry -/+=0/2` | 4 | UI preference draft | Product decision. Rebase/recreate if still wanted, otherwise close. |
| #6 `Restore book brand mark` | `codex/restore-book-logo` | `384bf58` | `ac95cb3` | `main_only=69`, `pr_only=1`, `cherry -/+=0/1` | 1 | UI preference draft | Product decision. Rebase/recreate if still wanted, otherwise close. |
| #1 `Implement realtime PM voice intake` | `codex/reset-workflow-architecture` | `300fe840` | `97bbe3a` | `main_only=180`, `pr_only=27`, `cherry -/+=3/24` | 40 | Very stale architecture experiment | Close or leave only as historical roadmap evidence. Any realtime PM work needs a new design note and fresh branch. |

Delta from older docs:

- `docs/maintenance/pr-triage.md` was dated 2026-06-17 and listed 34 open PRs.
- The older Wave 5 prep branch observed 7 open PRs.
- Current GitHub API returns 5 open PRs.
- Old stacked PRs #4, #12 through #33, #36, #38 through #48, and the Wave 5 #91/#92 drafts are not currently open.

## Cleanup Recommendations

1. Add or verify runtime trace points for the four `LEGACY_*` nodes before deleting any compatibility branch.
2. Do not merge #77. Use it only as historical evidence for a future BoardTask write extraction after current-main write trace coverage exists.
3. Migrate fallback explain next, because canonical BoardTask explain coverage is already strongest.
4. Migrate direct edit only after focused parity tests prove selection, missing focus, edit failure, and metadata behavior through BoardTask.
5. Preserve recent edit follow-up and autonomous location choice, but move both into target-resolution policy modules instead of expanding `chatbot.py`.
6. Close or refresh #80/#53/#6/#1 outside runtime cleanup; they are not blockers for compatibility branch cleanup.
7. Clean local maintenance clutter separately: `git worktree list --porcelain` shows prunable temporary worktrees, and `git remote show origin` shows stale local `origin/pr/*` refs. Do not prune them in this Worker K branch unless the maintainer asks.

## Blockers

- No authenticated `gh` session; PR audit used public GitHub API plus local git refs.
- Compatibility `NodeId` values are documented and defined, but code does not record them yet.
- Old document-action cleanup depends on BoardTask write/edit trace coverage; deleting first would risk losing commit metadata and failure history.

## Verification

Docs-only checks run for this branch:

- `git status --short`: showed docs-only changes in this worktree.
- `git diff --check`: passed.
- Domain-hardcode and fixed-HTML scan over changed docs: passed.

Runtime tests are not required for this report.

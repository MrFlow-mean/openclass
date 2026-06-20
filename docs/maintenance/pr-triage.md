# Open PR Triage and Branch Drift Audit

Audit date: 2026-06-20

Main audited: `origin/main` and GitHub `commits/main` at `80e246ade787e0342a39d3ce603f1208663e5ab0`.

Evidence commands used:

```bash
curl -sS 'https://api.github.com/repos/MrFlow-mean/openclass/pulls?state=open&per_page=100'
git fetch origin main --prune
git ls-remote origin refs/heads/main
git fetch origin pull/<PR>/head:refs/remotes/origin/pr/<PR>
git rev-list --left-right --count 80e246ade787e0342a39d3ce603f1208663e5ab0...origin/pr/<PR>
git cherry -v 80e246ade787e0342a39d3ce603f1208663e5ab0 origin/pr/<PR>
git diff --name-only 80e246ade787e0342a39d3ce603f1208663e5ab0...origin/pr/<PR>
```

Notes:

- All open PRs found by the GitHub API（接口） are currently `open` and `draft=true`.
- `gh`（GitHub CLI，GitHub 命令行工具） was not authenticated in this session, so the audit used the public GitHub API plus local `git` refs.
- `git cherry -v <main> <pr>` detects patch-equivalent commits already present on `main`; `-` means patch-equivalent to `main`, `+` means still unique to the PR branch.
- The older 2026-06-17 audit listed 34 open PRs. The current API result lists 5 open PRs.

## Summary Counts

| Classification | Count |
|---|---:|
| Current but behind-main draft | 1 |
| Stale handler extraction | 1 |
| UI preference draft requiring product decision | 2 |
| Very stale architecture experiment | 1 |
| Total open PRs audited | 5 |

## Maintainer Checklist

1. Do not merge #77 as-is. Use it only as historical evidence for a future BoardTask write extraction from current `main`.
2. Decide #53 and #6 as UI preference work. Rebase/recreate if still desired, otherwise close.
3. Rebase or recreate #80 if workflow test-helper extraction is still useful after current Wave 6 trace work.
4. Close #1 unless realtime PM voice intake is still an active roadmap experiment with a fresh design note.
5. Leave old closed PR stacks closed. Do not treat #4, #12 through #33, #36, #38 through #48, #91, or #92 as current open work.

## PR Matrix

| PR | Current state | Files touched | Likely overlap with `main` | Classification | Risk | Suggested next command or maintainer action |
|---|---|---|---|---|---|---|
| #80 `test: extract workflow test helpers` | Open draft, branch `codex/extract-workflow-test-helpers`; `main_only=15`, `pr_only=1`, `cherry -/+=0/1`. | 7 files under `apps/api/tests/board_task`, including `workflow_test_helpers.py`. | Unique test-helper commit only; branch is behind the required `MAIN_SHA`. | Current but behind-main draft. | Medium. | Rebase/recreate from current `main` before review; do not merge stale base directly. |
| #77 `Extract BoardTask write handler` | Open draft, branch `codex/extract-board-task-write-terminal`; `main_only=17`, `pr_only=1`, `cherry -/+=0/1`. | 3 files: `apps/api/app/services/chat/paths/board_task_write.py`, `apps/api/app/services/chatbot.py`, `apps/api/tests/test_ai_logging.py`. | Handler extraction idea remains useful, but the branch predates current trace ordering and should not be merged as-is. | Stale handler extraction. | High. | Close or keep only as reference; rebuild from current `main` after write trace coverage is stable. |
| #53 `[codex] Make favicon circular` | Open draft, branch `codex/circular-favicon`; `main_only=39`, `pr_only=2`, `cherry -/+=0/2`. | 4 web asset/layout files: favicon, icon, apple icon, layout metadata. | No patch-equivalent commit found on current `main`. | UI preference draft. | Low to medium. | Product decision: rebase/recreate if the circular favicon is still wanted, otherwise close. |
| #6 `Restore book brand mark` | Open draft, branch `codex/restore-book-logo`; `main_only=69`, `pr_only=1`, `cherry -/+=0/1`. | 1 file: `apps/web/src/components/brand-mark.tsx`. | No patch-equivalent commit found on current `main`. | UI preference draft. | Low. | Product decision: rebase/recreate if the book mark is still wanted, otherwise close. |
| #1 `Implement realtime PM voice intake` | Open draft, branch `codex/reset-workflow-architecture`; `main_only=180`, `pr_only=27`, `cherry -/+=3/24`. | 40 files across realtime router/service, AI workflow, course store/runtime, resource services, tests, and web realtime UI. | Large old architecture branch. Some patches are equivalent to `main`, but most realtime PM voice intake code remains only on the stale branch. | Very stale architecture experiment. | Very high. | Close or keep only as roadmap evidence. Any realtime PM work needs a new design note and fresh branch. |

## Recently No Longer Open

The current API result no longer lists these older stale stacks as open:

- #4
- #12 through #33
- #36
- #38 through #48
- #91
- #92

This does not prove every idea is obsolete. It only means they should not be used as current merge candidates. Recreate desired work from current `main` in fresh PR-sized slices.

## Suggested Cleanup Order

1. Close or refresh #80 and #77 after the Wave 6 coordinator decides whether test-helper extraction and BoardTask write extraction remain active.
2. Decide #53 and #6 as UI preferences.
3. Close #1 unless realtime PM voice intake has a current owner and design.
4. Run a separate local cleanup pass for stale `origin/pr/*` refs only after no worker depends on them.

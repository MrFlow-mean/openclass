# Course Studio Hooks

课程工作台的状态与副作用边界。顶层 [`course-studio.tsx`](../../components/course-studio.tsx) **只组合**，逻辑放这里。

## Hook 一览

| 文件 | 职责 |
|------|------|
| [`use-course-workspace.ts`](use-course-workspace.ts) | 课程包加载、active lesson、applyCoursePackage |
| [`use-board-draft.ts`](use-board-draft.ts) | 编辑器草稿、自动保存、与 server document 同步 |
| [`use-lesson-history.ts`](use-lesson-history.ts) | 分支、预览 commit、merge 流程 |
| [`use-lesson-chat-agent.ts`](use-lesson-chat-agent.ts) | Chat 消息、流式回复、interaction mode |
| [`use-workspace-actions.ts`](use-workspace-actions.ts) | 创建/移动/删除 lesson、课程包操作 |
| [`use-model-catalog.ts`](use-model-catalog.ts) | `/api/ai-models` 选择与持久化 |
| [`use-realtime-voice.ts`](use-realtime-voice.ts) | Realtime 语音连接（默认关闭） |

## 约定

- HTTP 一律通过 [`../../lib/api.ts`](../../lib/api.ts)，hook 内不 `fetch`
- 类型用 [`../../types/index.ts`](../../types/index.ts)，与后端 view 对齐
- 新增状态优先新 hook 或扩展现有 hook，避免回到 `course-studio.tsx` 堆 effect

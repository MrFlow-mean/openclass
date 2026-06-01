# Course Studio 组件

课程工作台 UI 子模块。组合入口：[`../course-studio.tsx`](../course-studio.tsx)。

## 组件地图

| 文件 | 职责 |
|------|------|
| [`course-studio-page-shell.tsx`](course-studio-page-shell.tsx) | 页面骨架、布局 |
| [`studio-side-panel.tsx`](studio-side-panel.tsx) | 左侧栏：标签、资源、图谱入口 |
| [`lesson-tabs.tsx`](lesson-tabs.tsx) | 打开中的 lesson 标签页 |
| [`board-editor-panel.tsx`](board-editor-panel.tsx) | 讲义编辑区容器 |
| [`word-board-editor.tsx`](word-board-editor.tsx) | 类 Word 富文本编辑器 |
| [`word-editor-toolbar.tsx`](word-editor-toolbar.tsx) | 编辑器工具栏 |
| [`chat-sidebar.tsx`](chat-sidebar.tsx) | Chat 侧栏 |
| [`lesson-history-graph-panel.tsx`](lesson-history-graph-panel.tsx) | 版本/分支图 |
| [`branch-merge-review-card.tsx`](branch-merge-review-card.tsx) | 合并预览 |
| [`resource-panel.tsx`](resource-panel.tsx) | 资料库列表 |
| [`course-graph-panel.tsx`](course-graph-panel.tsx) | 课程图谱 |
| [`history-utils.ts`](history-utils.ts) | 历史图数据转换（无 UI） |

## 约定

- 组件接收 props / callback，业务状态来自 [`../../hooks/course-studio/`](../../hooks/course-studio/)
- 单文件接近 800 行应继续拆分（见 [`../../../AGENTS.md`](../../../AGENTS.md)）
- i18n 用 `useInterfaceLanguage()`，文案在 [`../../lib/i18n/product-ui.ts`](../../lib/i18n/product-ui.ts)

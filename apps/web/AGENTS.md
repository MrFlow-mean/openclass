# 开放课堂 Web — AI 协作指南

仓库根 `AGENTS.md` 介绍整体架构、命令与后端约定。本文件覆盖前端独有约定。

<!-- BEGIN:nextjs-agent-rules -->
## 这不是你熟悉的 Next.js

仓库使用 Next.js 16，API、约定、文件结构都可能与你训练数据里的不一样。**写代码前先读 `node_modules/next/dist/docs/` 下对应指南，并尊重 deprecation 提示。**
<!-- END:nextjs-agent-rules -->

## 目录约定

- `src/app/`：App Router 页面入口。
- `src/components/`：重建区首页、个人主页、账户设置、基础 Chatbot 等顶层组件。
- `src/hooks/`：可复用交互状态与副作用，例如实时转写日志队列。
- `src/lib/`：API client、数学内容规范化、实时音频编解码等纯工具。
- `src/types/`：与后端 API 视图一一对应的 TypeScript 类型。

## 编码原则

- 旧 `course-studio` 工作台和对应 hooks 已下线。新产品工作链路要先明确 UI、API、service、schema、prompt、storage、test 的边界，再创建新的小模块。
- API 调用走 `src/lib/` 里的 client，组件层不直接 `fetch`。
- 视图类型放 `src/types/`，与后端返回字段一一对应；后端剥离了资料原文与本地路径，前端类型也不应包含。
- 实时音频 / PCM 编解码等底层逻辑放 `src/lib/`，组件只调用。
- 新模块接近 800-1200 行时优先拆边界，不要重新制造单文件总控制器。

## 命令

日常命令从仓库根执行：`npm run dev:web | lint:web | typecheck:web | build:web`。
前端主流程回归：`npm run test:e2e`。

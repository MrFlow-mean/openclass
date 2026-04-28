# 开放课堂 Web — AI 协作指南

仓库根 `AGENTS.md` 介绍整体架构、命令与后端约定。本文件覆盖前端独有约定。

<!-- BEGIN:nextjs-agent-rules -->
## 这不是你熟悉的 Next.js

仓库使用 Next.js 16，API、约定、文件结构都可能与你训练数据里的不一样。**写代码前先读 `node_modules/next/dist/docs/` 下对应指南，并尊重 deprecation 提示。**
<!-- END:nextjs-agent-rules -->

## 目录约定

- `src/app/`：App Router 页面入口。
- `src/components/`：课程工作台、学习首页、个人主页等顶层组件。
- `src/hooks/`：可复用交互状态与副作用，例如实时转写日志队列。
- `src/lib/`：API client、数学内容规范化、实时音频编解码等纯工具。
- `src/types/`：与后端 API 视图一一对应的 TypeScript 类型。

## 编码原则

- `course-studio.tsx` 已是最大的容器。新功能优先抽到 `hooks/` / `lib/` / 子组件再由它组合，**不要继续在这个文件里堆状态和 effect**。
- API 调用走 `src/lib/` 里的 client，组件层不直接 `fetch`。
- 视图类型放 `src/types/`，与后端返回字段一一对应；后端剥离了资料原文与本地路径，前端类型也不应包含。
- 实时音频 / PCM 编解码等底层逻辑放 `src/lib/`，组件只调用。

## 命令

日常命令从仓库根执行：`npm run dev:web | lint:web | typecheck:web | build:web`。

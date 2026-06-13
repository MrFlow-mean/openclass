# 开放课堂 Web 协作指南

根 `AGENTS.md` 管整体架构和 AI 链路；本文件只补前端边界。

## Next.js

仓库使用 Next.js 16。写 App Router、Server/Client Component、metadata、cache 或 route handler 前，先读 `node_modules/next/dist/docs/` 中对应指南，并按 deprecation 提示调整。

## 目录

- `src/app/`：App Router 页面入口。
- `src/components/`：顶层产品组件。
- `src/components/course-studio/`：课程工作台视图组件；`course-studio.tsx` 只做组合。
- `src/hooks/`：可复用交互状态和副作用。
- `src/hooks/course-studio/`：workspace、draft、history、resource/action、AI chat、model catalog、realtime voice 等工作台边界。
- `src/lib/`：API client、数学内容规范化、实时音频编解码等工具。
- `src/types/`：与后端 API 视图对应的 TypeScript 类型。

## 边界

- 新功能优先落在子组件、hook 或 `src/lib/`；不要继续把 state / effect 堆进 `course-studio.tsx`。
- 组件层不直接 `fetch`；API 调用走 `src/lib/` client。
- 前端类型必须匹配后端返回字段；后端剥离的资料原文和本地路径，前端类型也不应包含。
- 实时音频、PCM、低层协议放 `src/lib/`，组件只调用。
- `course-studio.tsx` 超过 1000 行要先拆；拆出的组件或 hook 接近 800-1200 行时继续拆边界。

## 命令

从仓库根执行：`npm run dev:web`、`npm run lint:web`、`npm run typecheck:web`、`npm run build:web`、`npm run test:e2e`。

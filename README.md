# OpenClass AI 课程工作台

OpenClass 是一个面向学习场景的 AI 课程工作台。它不是单轮问答窗口，而是把聊天、学习目标澄清、讲义编辑、版本历史、资料库和实时语音讲解放在同一个课程空间里。

核心体验：

- 左侧用聊天确认学习目标、理解程度和上下文。
- 右侧是类 Word 的富文本讲义，可以手动编辑、AI 局部改写、导入/导出 DOCX。
- 每节课有 commit、branch、restore，可以试不同讲法再回退。
- 课程包支持多 lesson、标签页工作区、课程图谱和资料库引用。
- 文本模型支持 OpenAI、Anthropic、Google、DeepSeek、Kimi、MiniMax、自定义 OpenAI-compatible 和 Anthropic-compatible provider。
- 实时语音支持 OpenAI Realtime 和 Google Gemini Live。

## 架构概览

```text
.
├── package.json                 # 根 workspace 脚本，统一安装、启动、验证
├── pyproject.toml               # 后端依赖、editable 安装与 pytest 配置
├── .env.example                 # 后端模型与运行时环境变量示例
├── apps/
│   ├── api/
│   │   ├── app/main.py           # FastAPI app 组装与健康检查
│   │   ├── app/routers/          # workspace / documents / chat / realtime / resources
│   │   ├── app/services/         # 状态、存储、AI、导入导出、历史等业务逻辑
│   │   └── data/                 # 本地运行数据，已在 .gitignore 中忽略
│   └── web/
│       ├── src/app/              # Next.js App Router 页面入口
│       ├── src/components/       # 课程工作台与首页组件
│       ├── src/hooks/            # 前端副作用和交互状态 hook
│       ├── src/lib/              # API、数学内容、实时音频等纯工具
│       └── src/types/            # 前后端共享的 TypeScript 视图类型
└── launcher/                     # 可双击打开的本地入口页
```

后端现在按「router 只处理 HTTP 边界，service 承担业务逻辑」拆分：

- `workspace`：课程包、lesson、打开标签页、移动和删除。
- `documents`：富文本保存、AI 编辑、DOCX 导入导出、branch、restore、proposal。
- `chat`：学习对话与课程内容生成。
- `realtime`：OpenAI WebRTC、Google Live WebSocket、实时转写日志。
- `resources`：资料上传和资料库索引。

`FileCourseStore` 使用锁和原子替换写入，避免并发保存时写出半截 JSON。API 返回的课程包视图会剥离资料原文和本地路径，减少前端暴露不必要数据。

## 本地运行

需要：

- Node.js 20 或更新版本。
- Python 3.13 或更新版本。

第一次安装：

```bash
npm run setup
```

后端环境变量可以从示例文件开始：

```bash
cp .env.example .env
```

启动前后端：

```bash
npm run dev
```

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`
- 健康检查：`http://localhost:8000/health`
- AI 输入输出日志：`apps/api/data/logs/ai-usage.jsonl`

也可以双击根目录的 `start-ai-board.command`。它会启动前端 `3000`、后端 `8000`，并打开 `launcher/personal-home.html`。

## 常用命令

```bash
npm run dev              # 同时启动前后端
npm run dev:web          # 只启动 Next.js
npm run dev:api          # 只启动 FastAPI
npm run lint:web         # 前端 lint
npm run typecheck:web    # 前端类型检查
npm run test:api         # 后端 pytest
npm run build:web        # 前端生产构建
npm run verify           # lint + typecheck + 后端测试 + 前端构建
```

后端依赖以根目录 `pyproject.toml` 为单一来源，根目录 `npm run setup:api` 会安装为 editable 包并带上测试依赖。

## 模型配置

最小配置示例：

```bash
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini
OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
```

更多 provider 可参考根目录 `.env.example`。常用覆盖项：

```bash
ANTHROPIC_API_KEY=your_anthropic_key_here
ANTHROPIC_MODEL=claude-opus-4-7

GOOGLE_API_KEY=your_google_gemini_key_here
GOOGLE_TEXT_MODEL=gemini-3.1-pro-preview
GOOGLE_REALTIME_MODEL=gemini-3.1-flash-live-preview

DEEPSEEK_API_KEY=your_deepseek_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

KIMI_API_KEY=your_kimi_key_here
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k2.6

MINIMAX_API_KEY=your_minimax_key_here
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M2.7
```

自定义兼容网关可以单独配置，不会覆盖官方 OpenAI / Anthropic：

```bash
OPENAI_COMPATIBLE_API_KEY=your_custom_openai_compatible_key_here
OPENAI_COMPATIBLE_BASE_URL=https://your-openai-compatible-host/v1
OPENAI_COMPATIBLE_MODEL=gpt-5-mini
OPENAI_COMPATIBLE_COMPAT_API=chat_completions

ANTHROPIC_COMPATIBLE_API_KEY=your_custom_anthropic_compatible_key_here
ANTHROPIC_COMPATIBLE_BASE_URL=https://your-anthropic-compatible-host
ANTHROPIC_COMPATIBLE_MODEL=claude-opus-4-7
```

前端“选择模型”会读取 `/api/ai-models`。文本模型支持 OpenAI、Anthropic、Google、DeepSeek、Kimi、MiniMax、自定义 OpenAI 兼容接口和自定义 Anthropic 兼容接口；实时语音模型只保留官方 OpenAI Realtime 与 Google Gemini Live。没有配置 Key 的 provider 会显示为未配置；没有可用文本模型时，后端会回退到本地启发式逻辑，方便离线开发。

## 当前实现范围

- 课程包、lesson、标签页工作区、课程图谱。
- 富文本讲义编辑、自动保存、手动 commit、branch、restore、diff preview。
- 聊天驱动的学习目标澄清、内容生成、局部文档编辑。
- 资料上传、PDF/DOCX/TXT 提取、资料引用。
- OpenAI / Anthropic / Google / DeepSeek / Kimi / MiniMax / 兼容 provider 文本模型选择。
- OpenAI Realtime / Google Gemini Live 语音讲师与转写日志。

## 后续架构建议

已完成的收口是“启动入口、后端路由、状态服务、依赖来源、前端实时语音工具”的第一轮整理。下一轮更值得做的是把 `CourseStudio` 继续按编辑器、聊天、模型选择、资源库、语音会话拆成更小的容器组件，并给 `workspace_state` 增加更细的事务 helper，减少 router 里重复的 load、mutate、save 模式。

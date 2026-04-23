# AI 黑板课程系统

这是一个面向教学场景的 AI 原生课程工作台：

- 左侧是聊天与学习需求澄清
- 右侧是块级板书文档
- 顶部支持像浏览器一样同时打开多节课
- 底层支持 commit / branch / history / lesson graph
- 后端预留 LangGraph 编排，支持 `PM AI -> Board AI -> Teacher AI`

## 本地运行

### 1. 启动后端依赖

项目已经按 `.venv` 约定准备好 Python 运行路径。若需要重新安装：

```bash
python3 -m venv .venv
.venv/bin/pip install -r apps/api/requirements.txt
```

### 2. 安装根目录脚本依赖

```bash
npm install
```

### 2.5. 配置 OpenAI

如果要启用真实 GPT 模型，请在运行前设置环境变量：

```bash
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-5.3
```

可选覆盖：

```bash
export OPENAI_PM_MODEL=gpt-5.3
export OPENAI_BOARD_MODEL=gpt-5.3
export OPENAI_GUIDE_MODEL=gpt-5.3
export OPENAI_TEACHER_MODEL=gpt-5.3
export OPENAI_LESSON_MODEL=gpt-5.3
export OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview
export OPENAI_REALTIME_VOICE=marin
```

如果没有设置 `OPENAI_API_KEY`，后端会自动回退到当前内置的启发式逻辑，方便继续本地开发。

### 3. 启动前后端

```bash
npm run dev
```

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`
- AI 输入输出日志：`apps/api/data/logs/ai-usage.jsonl`

### 4. 一键启动

如果你以后不想每次都手动输入命令，可以直接双击项目根目录里的：

```text
start-ai-board.command
```

它会：

- 启动前端 `3000`
- 启动后端 `8000`
- 打开本地启动页 `launcher/ai-board-launcher.html`

这个启动页会把当前前端原样嵌进去，所以你看到的仍然是现有那套页面，不是另一套重写的静态页。

## 目录结构

```text
apps/
  api/   FastAPI + LangGraph orchestration + file-backed course store
  web/   Next.js course studio UI
```

## 当前实现范围

- Phase 1：课程包、lesson、块级板书、手动编辑、commit、branch、restore、标签页工作区
- Phase 2：聊天驱动 patch proposal、diff preview、范围升级判断、课程资料库索引、LangGraph 工作流
- Phase 3：前端提供讲解朗读与讲师模式状态槽位
- Phase 4：保留课程图谱与版本模型，为后续社区协作继续扩展

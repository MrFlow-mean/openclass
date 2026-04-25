# AI 黑板课程系统

这是一个面向教学场景的 AI 原生课程工作台：

- 如果你已经厌倦了“问一句、答一句、下一次又从零开始”的普通 AI 学习方式，这个项目想做的是另一种体验：让 AI 像真正陪你上课的老师一样，一边和你确认你到底想学什么，一边把右侧讲义和板书持续整理出来。你不是在和一个只会聊天的窗口对话，而是在一点点搭建一份属于你自己的课程。
- 对学习者来说，`黑板 AI` 的价值不只是“回答问题”，而是把学习过程变成可沉淀、可回看、可继续扩写的内容资产。你可以让它按你的水平和目标生成一节课，可以直接改写某一段讲义，可以上传教材或笔记让它参考，也可以在不同思路之间开分支，不用担心一改就把原来的内容弄丢。
- 如果你想找的不是一个泛泛而谈的聊天机器人，而是一款能帮你“边学边整理、边问边成课、边迭代边保留版本”的学习工作台，那么这个黑板 AI 就是在往这个方向做。

## 为什么学习者会想选它

- 它不是只会回答，而是会把你的问题逐步长成一份完整讲义。
- 它会先理解你的学习目标、水平和场景，再决定讲多深、怎么讲。
- 它支持像 Word 一样直接编辑课程内容，不满意就改，不用反复复制粘贴。
- 它保留 history、commit、branch，你可以放心试错、回退、比较不同讲法。
- 它能接入你自己的教材、讲义、图片资料，让学习内容更贴近你的真实需要。
- 它不是一次性答案，而是一套可以持续积累的个人课程工作台。

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

# OpenClass Web

这是 OpenClass 的 Next.js 前端应用。根目录已经配置 npm workspace，日常命令建议从项目根目录执行。

```bash
npm run dev:web
npm run lint:web
npm run typecheck:web
npm run build:web
```

主要目录：

- `src/app/`：App Router 页面入口。
- `src/components/`：课程工作台、学习首页、个人主页等组件。
- `src/hooks/`：可复用交互状态和副作用，例如实时转写日志队列。
- `src/lib/`：API client、数学内容规范化、实时音频编解码等工具。
- `src/types/`：后端 API 视图对应的 TypeScript 类型。

`CourseStudio` 仍是最大的交互容器，新增功能优先放到 `hooks`、`lib` 或更小的子组件里，再由它组合调用。

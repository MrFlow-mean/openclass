# Web Lib（纯工具与 API）

无 React 依赖的工具、API client、mock 数据。

## 核心文件

| 文件 | 职责 |
|------|------|
| [`api.ts`](api.ts) | **唯一 HTTP 入口**；token 存储、错误 `detail.code` 解析 |
| [`account.ts`](account.ts) | 账号相关 localStorage 辅助 |
| [`auth-errors.ts`](auth-errors.ts) | 认证错误码到 UI 文案 |
| [`i18n/product-ui.ts`](i18n/product-ui.ts) | 产品 UI 中英词典 |
| [`open-courses.ts`](open-courses.ts) | 开放课程展示 helper |
| [`following.ts`](following.ts) | **Following 页 mock 数据**（非真实 API） |
| [`recent-feed.ts`](recent-feed.ts) | 首页 recent feed helper |
| [`streaming.ts`](streaming.ts) | SSE 解析 |
| [`realtime-audio.ts`](realtime-audio.ts) | PCM / WebRTC 音频工具 |

## ID 命名约定

- 领域对象：`UserView.id`、`PublicUserView.id`
- API 路径参数：client 方法用 `userId`（与 URL `/users/{userId}` 一致）
- Mock following：`FollowedCreator.id` 与 `FollowedCourseUpdate.creator_id` 成对引用

## 注意

- 组件层禁止直接 `fetch`，统一 `api.*`
- mock 数据（following、trending 部分收藏）不要误当作后端契约写进 `types/`

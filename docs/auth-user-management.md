# 登录与用户管理系统概览

## 后端认证（FastAPI + SQLite）

### 用户与数据表
核心表结构在 `apps/api/app/services/auth_service.py` 中自举：

| 表 | 用途 |
|---|---|
| `users` | 用户主表：id、email、phone、password_salt/hash、role、display_name、avatar_url |
| `auth_sessions` | 登录会话：token_hash → user_id，记录 created_at / last_seen_at |
| `auth_identities` | 多身份绑定：同一用户可关联 email、phone、google、github、wechat 等 OAuth 身份 |
| `auth_oauth_states` | OAuth 防 CSRF：state、provider、next_path、frontend_origin、guest_user_id、code_verifier |
| `auth_guest_sessions` | 游客会话：独立 token，不与 users 表关联 |

### 支持的身份方式

1. **邮箱/手机号 + 密码**（`register` / `login`）
   - 账号标识符自动识别邮箱或手机号（中国大陆 11 位）
   - PBKDF2-HMAC-SHA256，210,000 次迭代
   - 手机号的内部存储会 hash 为合成邮箱（`phone-{hash}@phone.openclass.local`）

2. **游客模式**（`/auth/guest`）
   - 自动生成 guest_user_id，数据仅在 `auth_guest_sessions`
   - 游客的工作区在注册/登录时可被“认领”迁移到新账号

3. **OAuth 2.0**（6 个 provider）
   - Google、Apple、GitHub、微信、Microsoft、X（Twitter）
   - 配置开关由环境变量控制，未配置则前端显示“未配置”
   - 支持 PKCE（X）、form_post（Apple）、微信特殊参数

### 权限与管理员

- 角色枚举：`user`、`admin`、`guest`
- **第一个注册用户自动为 `admin`**
- 环境变量 `OPENCLASS_ADMIN_EMAILS` 可额外指定管理员邮箱
- `/admin/overview` 接口受 `current_admin` 依赖保护（403 拦截）

### Token 机制

- 生成：`secrets.token_urlsafe(32)`
- 存储：SHA256 hash 后存库，原始 token 只返给客户端
- 传递优先级：Header `Authorization: Bearer <token>` > Cookie `openclass.auth.token` > Query `access_token`
- WebSocket 同样支持这三种取 token 方式

---

## 前端认证（Next.js）

### Token 存储策略
文件：`apps/web/src/lib/api.ts`

| 用户类型 | 存储位置 |
|---|---|
| 注册用户 | `localStorage` + Cookie（30 天） |
| 游客 | `sessionStorage` + Cookie（会话级） |

所有 API 请求自动带上 `Authorization: Bearer`；WebSocket URL 自动附加 `access_token` query。

### 路由与组件

- **`/login`、`/register`** → `AuthPanel`：登录/注册页，支持切换注册/登录、社交登录、游客登录
- **`/auth/callback`** → `AuthCallback`：OAuth 回调，取 token 后写 localStorage 并跳转
- **`AuthGate`**：通用路由守卫
  - 无 token 时自动跳 `/login?next=...`
  - 支持 `adminOnly` 模式，非 admin 显示无权限页
- **`AccountMenu`**：右上角头像下拉，显示用户信息、身份标签、管理后台入口、退出

---

## 用户管理界面

### 管理员后台（`/admin`）
文件：`apps/web/src/components/admin-dashboard.tsx`

- **仅只读展示**，无增删改操作
- 展示统计卡片：用户数、管理员数、课程包数、课程数、资料数
- 用户列表表格：账号、权限、注册时间、最近登录
- 无分页、无搜索、无禁用/删除/编辑用户功能

### 个人设置（`/profile?tab=settings`）
文件：`apps/web/src/components/profile-settings-panel.tsx`

- 大部分设置（显示名、handle、bio、主题、密度、通知偏好等）**只存在前端 `localStorage`**，**不同步后端**
- 唯一与后端联动的部分：调用 `/api/auth/me` 拉取当前用户真实信息（email、role、注册时间）
- **密码修改**：UI 有表单，但提交后仅显示 `s.password.notAvailable`，**后端没有修改密码接口**
- **模型偏好**：选择默认 text/realtime 模型，也是纯 localStorage

---

## 当前缺失的能力

1. **无密码修改 API**：前端有表单，后端未实现
2. **无密码找回/重置**：UI 预留，提示"接入邮件服务后即可发送重置链接"
3. **无邮箱验证**：注册即通过，无验证流程
4. **无用户管理操作**：管理员不能禁用、删除、修改其他用户
5. **无 profile 后端持久化**：个人资料修改仅存本地，换设备/清缓存即丢失
6. **无会话管理**：用户无法查看/踢掉其他登录设备
7. **无 rate limiting**：注册、登录、OAuth 均无请求频率限制

---

## 关键文件路径

| 功能 | 文件 |
|---|---|
| 后端认证路由 | `apps/api/app/routers/auth.py` |
| 后端认证逻辑 | `apps/api/app/services/auth_service.py` |
| 后端用户/会话模型 | `apps/api/app/models.py` |
| 前端 API + Token 管理 | `apps/web/src/lib/api.ts` |
| 登录/注册页组件 | `apps/web/src/components/auth-panel.tsx` |
| 路由守卫 | `apps/web/src/components/auth-gate.tsx` |
| OAuth 回调 | `apps/web/src/components/auth-callback.tsx` |
| 管理员后台 | `apps/web/src/components/admin-dashboard.tsx` |
| 个人设置面板 | `apps/web/src/components/profile-settings-panel.tsx` |
| 账号下拉菜单 | `apps/web/src/components/account-menu.tsx` |

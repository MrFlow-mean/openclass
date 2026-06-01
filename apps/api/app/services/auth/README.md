# Auth 模块

用户注册、登录、会话、OAuth 与管理员操作。实现文件在**上级目录**（flat 结构，本目录仅文档）。

## 核心文件

| 文件 | 职责 |
|------|------|
| [`../auth_service.py`](../auth_service.py) | 注册/登录/游客/OAuth、会话签发与撤销、admin 操作 |
| [`../auth_store.py`](../auth_store.py) | SQLite：`users`、`auth_sessions`、`auth_identities`、`auth_guest_sessions` |
| [`../email_delivery.py`](../email_delivery.py) | 验证邮件、密码重置（`OPENCLASS_EMAIL_DELIVERY=log` 写本地日志） |

## 关键约束

- **Token 只存 SHA256 hash**，原始 token 仅返回客户端一次
- **第一个注册用户自动为 `admin`**；`OPENCLASS_ADMIN_EMAILS` 可追加管理员
- **游客 workspace** 在注册/登录时可被认领到正式账号（避免丢数据）
- OAuth 未验证邮箱合并规则在 service 内统一处理，router 不重复判断

## HTTP 入口

[`../../routers/auth.py`](../../routers/auth.py) — `/api/auth/*`、`/api/admin/*`

## 延伸阅读

- 架构详述：[`../../../../../docs/auth-user-management.md`](../../../../../docs/auth-user-management.md)
- 测试：[`../../../tests/test_auth_service.py`](../../../tests/test_auth_service.py)、[`../../../tests/test_http_auth.py`](../../../tests/test_http_auth.py)

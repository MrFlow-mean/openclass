# Collaboration 模块

开放课程：发布课程包、fork、提交改进、维护者审核合并。

## 核心文件

| 文件 | 职责 |
|------|------|
| [`../collaboration.py`](../collaboration.py) | `CourseCollaborationService`：publish / fork / contribution / merge / maintainers |

## 关键约束

- **Fork 复制完整课程包与资料文件**到新 upload 路径，不共享原文件句柄
- **Contribution merge** 在事务内写回主线 lesson 与 history commit
- 只有 **maintainer / owner** 可 review merge；contributor 不能 merge 自己的 PR
- 读写 workspace 走 `load_workspace_for_user` / `save_workspace_for_user`（与 [`../workspace_state.py`](../workspace_state.py) 一致）

## HTTP 入口

[`../../routers/collaboration.py`](../../routers/collaboration.py)

## 测试

- Service：[`../../../tests/test_collaboration.py`](../../../tests/test_collaboration.py)
- HTTP：[`../../../tests/test_http_collaboration.py`](../../../tests/test_http_collaboration.py)

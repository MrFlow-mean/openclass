from __future__ import annotations

from typing import Any


WORKFLOW_REMOVED_DETAIL = "后端 AI 工作流程运行框架已移除，新的产品工作架构等待接入。"


class RemovedCourseWorkflow:
    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(WORKFLOW_REMOVED_DETAIL)


course_workflow = RemovedCourseWorkflow()

from app.services.workflow_roles.pm import generate_pm_interview_message

__all__ = [
    "generate_pm_interview_message",
    "run_board_manager",
    "run_board_executor",
    "run_teacher",
]


def __getattr__(name: str):
    if name == "run_board_manager":
        from app.services.workflow_roles.board_manager import run_board_manager

        return run_board_manager
    if name == "run_board_executor":
        from app.services.workflow_roles.board_executor import run_board_executor

        return run_board_executor
    if name == "run_teacher":
        from app.services.workflow_roles.teacher import run_teacher

        return run_teacher
    raise AttributeError(name)

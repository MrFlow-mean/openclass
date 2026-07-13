from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks


class ResearchArtifactJobRunner:
    """Runs persisted artifact jobs after an HTTP response without duplicating active work."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research-artifact")

    def schedule(
        self,
        background_tasks: BackgroundTasks,
        *,
        artifact_id: str,
        process: Callable[[], object],
    ) -> None:
        background_tasks.add_task(self._run_once, artifact_id, process)

    def submit(self, *, artifact_id: str, process: Callable[[], object]) -> None:
        """Resume a persisted job independently of an initiating HTTP request."""
        self._executor.submit(self._run_once, artifact_id, process)

    def is_active(self, artifact_id: str) -> bool:
        with self._lock:
            return artifact_id in self._active

    def _run_once(self, artifact_id: str, process: Callable[[], object]) -> None:
        with self._lock:
            if artifact_id in self._active:
                return
            self._active.add(artifact_id)
        try:
            process()
        except Exception:
            # The workspace persists the failed state and diagnostic message. A
            # background task must not turn that handled job failure into an
            # unhandled HTTP server exception.
            pass
        finally:
            with self._lock:
                self._active.discard(artifact_id)


research_artifact_job_runner = ResearchArtifactJobRunner()

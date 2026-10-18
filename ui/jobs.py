"""Background jobs: one at a time, each in a thread, with a progress message and percent the page polls."""
from __future__ import annotations

import logging
import threading
import time
import uuid

log = logging.getLogger("doctest-ui")


class JobError(Exception):
    """A failure with a short message that is safe to show in the page."""


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._running: str | None = None

    def running(self) -> dict | None:
        with self._lock:
            return dict(self._jobs[self._running]) if self._running else None

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def start(self, kind: str, fn) -> str:
        """Run fn(progress) in a thread. progress(message, percent) updates the job. Raises if busy."""
        with self._lock:
            if self._running:
                raise JobError("Another job is still running. Wait for it to finish.")
            job_id = uuid.uuid4().hex[:12]
            self._jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "message": "Starting...",
                                  "percent": 0, "started": time.time(), "error": None}
            self._running = job_id

        def progress(message: str, percent: float | None = None):
            with self._lock:
                self._jobs[job_id]["message"] = message
                if percent is not None:
                    self._jobs[job_id]["percent"] = max(0, min(100, int(percent)))

        def run():
            try:
                summary = fn(progress)
                with self._lock:
                    self._jobs[job_id].update(status="done", percent=100, message=summary or "Done")
            except JobError as e:
                log.warning("job %s (%s) failed: %s", job_id, kind, e)
                with self._lock:
                    self._jobs[job_id].update(status="failed", error=str(e), message=str(e))
            except Exception as e:  # noqa: BLE001 - full details to the log, short message to the page
                log.exception("job %s (%s) crashed", job_id, kind)
                with self._lock:
                    self._jobs[job_id].update(status="failed", error=f"Unexpected error: {e.__class__.__name__}. "
                                                                     "See the server log for details.",
                                              message="Failed")
            finally:
                with self._lock:
                    self._running = None

        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return job_id

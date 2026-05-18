"""In-memory job state for the TradingAgents web backend.

Enforces a single concurrent analysis job via a global mutex, and runs a
per-job watchdog timer that sets the job's stop_event and marks it ERROR
if the analysis exceeds the timeout (default 600s). State is in-memory
only — jobs do not survive a server restart.
"""
from __future__ import annotations
import threading
import uuid
from enum import Enum
from typing import Callable, Optional


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class JobNotFoundError(Exception):
    pass


class _Job:
    def __init__(self, job_id: str, watchdog_timeout: float):
        self.job_id = job_id
        self.status = JobStatus.PENDING
        self.stop_event = threading.Event()
        self.report: Optional[str] = None
        self.error_message: Optional[str] = None
        self._watchdog_timeout = watchdog_timeout
        self._watchdog_timer: Optional[threading.Timer] = None

    def start_watchdog(self, on_timeout: Callable[[], None]) -> None:
        self._watchdog_timer = threading.Timer(self._watchdog_timeout, on_timeout)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def cancel_watchdog(self):
        if self._watchdog_timer:
            self._watchdog_timer.cancel()


class JobManager:
    def __init__(self, watchdog_timeout: float = 600.0):
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._running_job_id: Optional[str] = None
        self._watchdog_timeout = watchdog_timeout

    def create_job(self) -> str:
        """Create a new PENDING job and return its id."""
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = _Job(job_id, self._watchdog_timeout)
        return job_id

    def _get(self, job_id: str) -> _Job:
        if job_id not in self._jobs:
            raise JobNotFoundError(f"Job {job_id} not found")
        return self._jobs[job_id]

    def start_job(self, job_id: str) -> None:
        """Start a job and activate its watchdog timer."""
        with self._lock:
            if self._running_job_id is not None:
                raise RuntimeError(
                    f"Job {self._running_job_id} already running"
                )
            job = self._get(job_id)
            job.status = JobStatus.RUNNING
            self._running_job_id = job_id
            job.start_watchdog(lambda: self._watchdog_fire(job_id))

    def _watchdog_fire(self, job_id: str) -> None:
        try:
            job = self._get(job_id)
            if job.status == JobStatus.RUNNING:
                job.stop_event.set()
                self.error_job(job_id, "分析超时（10分钟）")
        except JobNotFoundError:
            pass

    def finish_job(self, job_id: str) -> None:
        """Mark a job as DONE and cancel its watchdog timer."""
        with self._lock:
            job = self._get(job_id)
            job.cancel_watchdog()
            job.status = JobStatus.DONE
            if self._running_job_id == job_id:
                self._running_job_id = None

    def error_job(self, job_id: str, message: str = "") -> None:
        """Mark a job as ERROR with an optional error message."""
        with self._lock:
            job = self._get(job_id)
            job.cancel_watchdog()
            job.status = JobStatus.ERROR
            job.error_message = message
            if self._running_job_id == job_id:
                self._running_job_id = None

    def get_status(self, job_id: str) -> JobStatus:
        """Get the current status of a job."""
        return self._get(job_id).status

    def get_stop_event(self, job_id: str) -> threading.Event:
        """Return the stop event for a job."""
        return self._get(job_id).stop_event

    def has_running_job(self) -> bool:
        """Check if a job is currently running."""
        return self._running_job_id is not None

    def set_report(self, job_id: str, content: str) -> None:
        """Store the report content for a job."""
        self._get(job_id).report = content

    def get_report(self, job_id: str) -> Optional[str]:
        """Get the report content for a job, or None if not set."""
        return self._get(job_id).report

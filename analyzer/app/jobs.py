"""In-memory job registry for async /analyze runs.

Replaces the old synchronous pattern (HTTP request held open for 5–15 min)
with: POST starts a background thread and returns a job_id immediately,
GET /jobs/{id} polls status until result is ready.

Trade-off: jobs are in-memory only. A process restart drops in-flight
jobs; the user has to retry. For this single-user analyzer that's fine —
finished jobs are already persisted to analyzer_runs in Supabase.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

log = logging.getLogger("analyzer.jobs")


@dataclass
class Job:
    id: str
    status: str = "queued"            # queued | running | done | error
    progress_pct: float = 0.0          # 0–100, advisory only
    stage: str = ""                    # short message: "fetching trades…"
    detail: str = ""                   # longer message, optional
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None
    # Tag the wallet we're analysing so the UI can pin the right job
    wallet: str = ""


_lock = threading.Lock()
_jobs: dict[str, Job] = {}
# Soft cap: hold finished jobs for an hour, then drop. Keeps memory bounded.
_RETENTION_SECONDS = 3600


def _gc() -> None:
    """Drop finished jobs older than retention; called inside the lock."""
    cutoff = time.time() - _RETENTION_SECONDS
    stale = [jid for jid, j in _jobs.items()
             if j.finished_at is not None and j.finished_at < cutoff]
    for jid in stale:
        _jobs.pop(jid, None)


def new_job(wallet: str) -> Job:
    """Create a job in queued state and return it."""
    jid = uuid.uuid4().hex[:12]
    job = Job(id=jid, wallet=wallet.lower(), status="queued")
    with _lock:
        _gc()
        _jobs[jid] = job
    return job


def get_job(jid: str) -> Job | None:
    with _lock:
        return _jobs.get(jid)


def to_dict(job: Job) -> dict:
    d = asdict(job)
    # Don't dump giant result blobs from the polling endpoint until done
    if job.status != "done":
        d.pop("result", None)
    return d


def run_in_thread(job: Job, target: Callable[["ProgressReporter"], dict]) -> None:
    """Launch `target` in a background thread, wiring a progress reporter
    that updates this job's status as the work proceeds. `target` must
    return a result dict on success or raise on failure.
    """
    def _wrapped() -> None:
        reporter = ProgressReporter(job)
        try:
            reporter.update("running", 1.0, "starting…")
            result = target(reporter)
            with _lock:
                job.status = "done"
                job.progress_pct = 100.0
                job.stage = "complete"
                job.detail = ""
                job.result = result
                job.finished_at = time.time()
            log.info(f"job {job.id} done ({job.wallet})")
        except Exception as e:
            log.exception(f"job {job.id} failed")
            with _lock:
                job.status = "error"
                job.error = str(e)
                job.finished_at = time.time()

    t = threading.Thread(target=_wrapped, name=f"job-{job.id}", daemon=True)
    t.start()


class ProgressReporter:
    """Passed to the worker function so it can update the job's progress."""

    def __init__(self, job: Job) -> None:
        self._job = job

    def update(self, status: str | None = None, pct: float | None = None,
               stage: str | None = None, detail: str | None = None) -> None:
        with _lock:
            if status is not None:
                self._job.status = status
            if pct is not None:
                self._job.progress_pct = max(0.0, min(100.0, pct))
            if stage is not None:
                self._job.stage = stage
            if detail is not None:
                self._job.detail = detail

    @property
    def cancelled(self) -> bool:
        # Reserved for a future cancel feature
        return False

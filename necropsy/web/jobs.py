"""The background job runner for cache-miss analyses (R-1.5, R-3.6, E14).

Cache-miss work never runs on a request thread: a 20-second analysis would hang
the browser and, worse, tie up a worker the gallery needs for cache hits. So the
POST enqueues a job, returns a `job_id`, and the browser polls. A bounded pool of
two workers runs the analyses; single-flight coalescing inside `AnalysisService`
means two people pasting the same URL still only pay for one analysis.

The runner keeps the per-job *stage* in memory so the browser's progress copy is
driven by where the pipeline actually is ("reading the file tree"), not a timer.
The durable job record lives in SQLite via `store.jobs`; this class holds only
the live futures and the latest stage string.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from necropsy.analysis.service import AnalysisService
from necropsy.errors import NecropsyError, wrap_unexpected
from necropsy.store import jobs as jobs_store
from necropsy.store.db import Database

log = logging.getLogger("necropsy.web.jobs")

#: Two workers: enough for the single-presenter demo, bounded so a burst cannot
#: exhaust the box (E14). Cache hits never enter this pool.
MAX_WORKERS = 2


@dataclass
class JobHandle:
    """Live state for one running analysis. The durable record is in SQLite."""

    job_id: str
    canonical_url: str
    stage: str = "queued"
    future: Future | None = field(default=None, repr=False)


class JobRunner:
    """Owns the worker pool and the live job handles."""

    def __init__(self, db: Database, service: AnalysisService) -> None:
        self.db = db
        self.service = service
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="necropsy")
        self._handles: dict[str, JobHandle] = {}
        self._lock = threading.Lock()

    def submit(self, url: str) -> str:
        """Enqueue an analysis. Returns a job id the client polls."""
        with self.db.transaction() as conn:
            job = jobs_store.create(conn, url)
        handle = JobHandle(job_id=job.job_id, canonical_url=url)
        with self._lock:
            self._handles[job.job_id] = handle
        handle.future = self._pool.submit(self._run, handle)
        return job.job_id

    def _run(self, handle: JobHandle) -> None:
        job_id = handle.job_id
        try:
            with self.db.transaction() as conn:
                jobs_store.mark_running(conn, job_id)

            def on_stage(stage: str) -> None:
                handle.stage = stage

            result = self.service.analyze(handle.canonical_url, on_stage=on_stage)
            row = result.row
            analysis_id = row.id if row is not None else None
            with self.db.transaction() as conn:
                if analysis_id is not None:
                    jobs_store.mark_done(conn, job_id, analysis_id=analysis_id)
                else:  # a coalesced hit with no row handle; resolve by slug later
                    jobs_store.mark_done(conn, job_id, analysis_id=0)
            handle.stage = "done"
            # Stash the slug so the poll endpoint can redirect without a lookup race.
            handle.canonical_url = result.analysis.slug
        except NecropsyError as error:
            log.info("analysis job %s failed: %s", job_id, error.code)
            with self.db.transaction() as conn:
                jobs_store.mark_failed(conn, job_id, code=error.code, message=error.message)
            handle.stage = "failed"
        except Exception as exc:  # noqa: BLE001 - the worker's error boundary
            mapped = wrap_unexpected(exc)
            log.exception("analysis job %s raised", job_id)
            with self.db.transaction() as conn:
                jobs_store.mark_failed(conn, job_id, code=mapped.code, message=mapped.message)
            handle.stage = "failed"

    def stage_of(self, job_id: str) -> str:
        with self._lock:
            handle = self._handles.get(job_id)
        return handle.stage if handle else "unknown"

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

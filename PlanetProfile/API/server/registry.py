""" In-memory job registry: state machine, jobdir lifecycle, and SSE fan-out. """
import os
import time
import uuid
import asyncio
import shutil
from typing import Optional, Dict, List

# Terminal statuses the worker may report; the server maps everything else onto these.
TERMINAL = {'succeeded', 'invalid', 'failed', 'canceled'}


class Job:
    def __init__(self, jid: str, spec: dict, jobdir: str):
        self.id = jid
        self.spec = spec
        self.body = spec.get('body')
        self.mode = (spec.get('mode') or 'single').lower()
        self.jobdir = jobdir
        self.status = 'queued'
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.error: Optional[dict] = None
        self.summary: Optional[dict] = None
        self.meta: Optional[dict] = None
        self.manifest: Optional[list] = None
        self.cancel = asyncio.Event()
        self.done = asyncio.Event()
        self.worker = None                       # set while running (for cancel -> kill)
        self._subscribers: List[asyncio.Queue] = []
        self._event_log: List[dict] = []

    # -- SSE fan-out --------------------------------------------------------
    async def emit(self, event: dict):
        self._event_log.append(event)
        for q in list(self._subscribers):
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for e in self._event_log:                # replay so a late subscriber isn't behind
            q.put_nowait(e)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    # -- serialization ------------------------------------------------------
    def public(self) -> dict:
        d = {'id': self.id, 'body': self.body, 'mode': self.mode, 'status': self.status,
             'createdAt': self.created_at, 'startedAt': self.started_at,
             'finishedAt': self.finished_at}
        if self.status in TERMINAL:
            if self.error is not None:
                d['error'] = self.error
            if self.summary is not None:
                d['summary'] = self.summary
            if self.meta is not None:
                d['meta'] = self.meta
        return d


class JobRegistry:
    def __init__(self, runs_dir: str, max_jobs: int = 500):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)
        self.jobs: Dict[str, Job] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.max_jobs = max_jobs           # hard count cap (in addition to the time-based reaper)

    def _evict_oldest_terminal(self):
        """ Purge the oldest terminal jobs to keep the registry under max_jobs. """
        terminal = sorted((j for j in self.jobs.values() if j.status in TERMINAL),
                          key=lambda j: j.finished_at or j.created_at)
        for j in terminal[:max(1, len(self.jobs) // 10)]:
            self.purge(j.id)

    def create(self, spec: dict) -> Job:
        if len(self.jobs) >= self.max_jobs:
            self._evict_oldest_terminal()
        jid = uuid.uuid4().hex
        jobdir = os.path.join(self.runs_dir, jid)
        os.makedirs(jobdir, exist_ok=True)
        job = Job(jid, spec, jobdir)
        self.jobs[jid] = job
        return job

    def get(self, jid: str) -> Optional[Job]:
        return self.jobs.get(jid)

    def list(self, limit: int = 100) -> List[dict]:
        jobs = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.public() for j in jobs[:limit]]

    def purge(self, jid: str):
        job = self.jobs.pop(jid, None)
        if job is not None:
            shutil.rmtree(job.jobdir, ignore_errors=True)

    def reap(self, ttl_s: float):
        """ Delete jobdirs (and drop registry entries) for terminal jobs older than ttl. """
        now = time.time()
        for jid in list(self.jobs):
            job = self.jobs[jid]
            if job.status in TERMINAL and job.finished_at and (now - job.finished_at) > ttl_s:
                self.purge(jid)

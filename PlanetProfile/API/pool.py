""" Asyncio warm-worker pool for the FastAPI backend.

Supervises N long-lived ``ppworker`` subprocesses (``python -m PlanetProfile.API.ppworker``),
each of which imports the engine once and then runs one job at a time over a JSONL
stdin/stdout channel (see ppworker.py for the protocol). The pool hands a free worker to
one job at a time (the engine is non-reentrant), streams the worker's progress messages
to a callback, and kills+respawns a worker on crash, timeout, or cancellation.

Nothing here imports the engine — the heavy PlanetProfile import lives only in the worker
subprocesses, so a native crash (Reaktoro/CSPICE) takes down one worker, never the server.
"""
import os
import sys
import json
import asyncio
import logging

log = logging.getLogger('PlanetProfile.API.pool')


class WorkerError(Exception):
    """ Base class for worker-pool failures. """


class WorkerCrashed(WorkerError):
    """ The worker process died (stdout EOF / nonzero exit) before a terminal message. """


class Worker:
    """ One ppworker subprocess: import-once, one job in-flight, JSONL over stdin/stdout. """

    def __init__(self, wid, cmd, cwd, env):
        self.wid = wid
        self._cmd = cmd
        self._cwd = cwd
        self._env = env
        self.proc = None
        self.version = None
        self.jobs_done = 0            # for recycling: bound long-lived worker RSS growth

    async def start(self, ready_timeout=180.0):
        """ Spawn the subprocess and block until it emits its ``ready`` message. """
        self.proc = await asyncio.create_subprocess_exec(
            *self._cmd, cwd=self._cwd, env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)  # engine logs go to the worker's stderr
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=ready_timeout)
        except asyncio.TimeoutError:
            await self.kill()
            raise WorkerCrashed(f'worker {self.wid} timed out before ready')
        if not line:
            raise WorkerCrashed(f'worker {self.wid} exited before ready')
        try:                                          # kill (don't leak) the child on a bad handshake
            msg = json.loads(line)
            if msg.get('type') != 'ready':
                raise WorkerCrashed(f'worker {self.wid} first message was not "ready": {msg.get("type")}')
        except Exception:
            await self.kill()
            raise
        self.version = msg.get('version')
        log.debug(f'worker {self.wid} ready (pid {msg.get("pid")}, engine {self.version})')

    async def run_job(self, jobid, spec, jobdir, on_progress=None):
        """ Hand one job to this worker; forward progress; return the terminal result dict.

            Raises WorkerCrashed if the process dies before a terminal ``result`` message
            (the caller then respawns the worker). Cancelling the awaiting task (e.g. on
            timeout/cancel) should be followed by kill()+respawn, since a running native
            call cannot be interrupted otherwise.
        """
        job = {'type': 'job', 'id': jobid, 'spec': spec, 'jobdir': jobdir}
        self.proc.stdin.write((json.dumps(job) + '\n').encode())
        await self.proc.stdin.drain()
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise WorkerCrashed(f'worker {self.wid} died mid-job {jobid}')
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # stdout is a pristine JSON channel; ignore any stray line defensively
            mtype = msg.get('type')
            if mtype == 'progress':
                if on_progress is not None:
                    await on_progress(msg)
            elif mtype == 'result':
                self.jobs_done += 1
                return msg
            # ignore a stray 'ready' mid-job

    async def kill(self):
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    async def quit(self):
        """ Ask the worker to exit cleanly; kill if it doesn't. """
        try:
            self.proc.stdin.write(b'{"type":"quit"}\n')
            await self.proc.stdin.drain()
            await asyncio.wait_for(self.proc.wait(), timeout=5.0)
        except Exception:
            await self.kill()


class WorkerPool:
    """ A fixed-size pool of warm workers with a free-list. One job per worker at a time. """

    def __init__(self, n_workers, python=None, cwd=None, env=None, max_jobs_per_worker=64):
        self.n_workers = max(1, int(n_workers))
        self.python = python or sys.executable
        self.cwd = cwd
        self.extra_env = env or {}
        self.max_jobs_per_worker = int(max_jobs_per_worker)   # 0 = unlimited; else recycle to cap RSS
        self._free = asyncio.Queue()
        self._workers = []
        self._counter = 0
        self._closing = False

    def should_recycle(self, w):
        """ Whether a worker has done enough jobs that it should be recycled instead of reused
            (bounds the engine's per-run memory growth in a long-lived worker). """
        return self.max_jobs_per_worker and getattr(w, 'jobs_done', 0) >= self.max_jobs_per_worker

    def _worker_env(self):
        env = dict(os.environ)
        env.update(self.extra_env)
        env.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')   # torch/OpenMP on macOS; harmless elsewhere
        return env

    async def _spawn(self):
        self._counter += 1
        w = Worker(self._counter, [self.python, '-m', 'PlanetProfile.API.ppworker'],
                   self.cwd, self._worker_env())
        await w.start()
        self._workers.append(w)
        return w

    async def start(self):
        """ Spawn all workers concurrently and place them on the free-list. """
        workers = await asyncio.gather(*[self._spawn() for _ in range(self.n_workers)])
        for w in workers:
            self._free.put_nowait(w)
        log.info(f'worker pool ready: {len(workers)} workers')

    async def acquire(self):
        """ Await and return a free worker (backpressure = concurrency cap). """
        return await self._free.get()

    def release(self, w):
        """ Return a healthy worker to the free-list. """
        if not self._closing:
            self._free.put_nowait(w)

    async def replace(self, w):
        """ Kill a dead/cancelled/recycled worker and spawn a fresh one onto the free-list.

            Retries the spawn so a transient failure does not permanently shrink the pool
            (a shrunk-to-zero pool would deadlock acquire()); logs critically only if every
            attempt fails. """
        await w.kill()
        if w in self._workers:
            self._workers.remove(w)
        if self._closing:
            return
        for attempt in range(3):
            try:
                nw = await self._spawn()
                self._free.put_nowait(nw)
                return
            except Exception as e:                       # noqa: BLE001
                log.error(f'respawn attempt {attempt + 1}/3 failed: {e}')
                await asyncio.sleep(0.5 * (attempt + 1))
        log.critical(f'could not respawn a worker after 3 attempts; pool degraded to '
                     f'{len(self._workers)} worker(s)')

    def ready_count(self):
        return self._free.qsize()

    def total_count(self):
        return len(self._workers)

    async def close(self):
        self._closing = True
        await asyncio.gather(*[w.quit() for w in list(self._workers)], return_exceptions=True)

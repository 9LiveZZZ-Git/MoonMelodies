""" The FastAPI application: lifespan (worker pool + dispatcher), middleware, routing. """
import os
import time
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from PlanetProfile.API.pool import WorkerPool, WorkerCrashed
from PlanetProfile.API.server.config import ServerConfig
from PlanetProfile.API.server.registry import JobRegistry, TERMINAL
from PlanetProfile.API.server.security import HostGuardMiddleware, PrivateNetworkMiddleware
from PlanetProfile.API.server import routes_meta, routes_runs, routes_events, routes_artifacts

log = logging.getLogger('PlanetProfile.API.server')


def _apply_result(job, result: dict):
    """ Fold a worker's terminal ``result`` message onto the job. """
    status = result.get('status', 'failed')
    job.status = status if status in TERMINAL else 'failed'
    job.error = result.get('error')
    job.summary = result.get('summary')
    job.meta = result.get('meta')
    job.manifest = result.get('manifest')


async def _run_one(app, job):
    """ Drive one job on one worker: acquire -> run (racing cancel + timeout) -> terminal. """
    pool: WorkerPool = app.state.pool
    cfg: ServerConfig = app.state.config

    worker = await pool.acquire()
    # The job may have been canceled while queued/awaiting a worker.
    if job.cancel.is_set() or job.status == 'canceled':
        pool.release(worker)
        job.status = 'canceled'
        job.finished_at = time.time()
        await job.emit({'type': 'status', 'id': job.id, 'status': 'canceled', 'terminal': True})
        job.done.set()
        return

    job.worker = worker
    job.status = 'running'
    job.started_at = time.time()
    await job.emit({'type': 'status', 'id': job.id, 'status': 'running'})

    async def on_progress(msg):
        await job.emit(msg)

    run_task = asyncio.ensure_future(worker.run_job(job.id, dict(job.spec), job.jobdir, on_progress))
    cancel_task = asyncio.ensure_future(job.cancel.wait())
    try:
        done, _pending = await asyncio.wait({run_task, cancel_task},
                                            timeout=cfg.job_timeout_s,
                                            return_when=asyncio.FIRST_COMPLETED)
        if run_task in done:
            _apply_result(job, run_task.result())          # may raise WorkerCrashed
            if pool.should_recycle(worker):                # bound long-lived worker RSS growth
                await pool.replace(worker)
            else:
                pool.release(worker)
        else:
            run_task.cancel()
            if cancel_task in done:
                job.status = 'canceled'
                job.error = {'code': 'canceled', 'message': 'canceled by client'}
            else:
                job.status = 'failed'
                job.error = {'code': 'timeout', 'message': f'exceeded {cfg.job_timeout_s:.0f}s wall-clock'}
            await pool.replace(worker)                     # kill + respawn (native call can't be interrupted)
    except WorkerCrashed as e:
        job.status = 'failed'
        job.error = {'code': 'worker_crash', 'message': str(e)}
        await pool.replace(worker)
    except Exception as e:                                 # noqa: BLE001
        job.status = 'failed'
        job.error = {'code': 'server_error', 'message': str(e)}
        await pool.replace(worker)
    finally:
        cancel_task.cancel()
        job.worker = None
        job.finished_at = time.time()
        await job.emit({'type': 'status', 'id': job.id, 'status': job.status, 'terminal': True})
        job.done.set()


async def _dispatcher(app):
    """ Pull queued job ids and launch a per-job task (backpressure via the pool free-list). """
    reg: JobRegistry = app.state.registry
    while True:
        jid = await reg.queue.get()
        job = reg.get(jid)
        if job is None or job.status != 'queued':
            continue
        asyncio.ensure_future(_run_one(app, job))


async def _reaper(app):
    reg: JobRegistry = app.state.registry
    cfg: ServerConfig = app.state.config
    while True:
        await asyncio.sleep(3600)
        try:
            reg.reap(cfg.jobdir_ttl_s)
        except Exception as e:                             # noqa: BLE001
            log.warning(f'reaper error: {e}')


def build_app(config: ServerConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.registry = JobRegistry(config.runs_dir)
        app.state.pool = WorkerPool(config.workers)
        log.info(f'starting {config.workers} warm workers...')
        await app.state.pool.start()
        app.state.tasks = [asyncio.ensure_future(_dispatcher(app)),
                           asyncio.ensure_future(_reaper(app))]
        try:
            yield
        finally:
            for t in app.state.tasks:
                t.cancel()
            await app.state.pool.close()

    app = FastAPI(title='MoonMelodies backend', version='1.0', lifespan=lifespan)
    app.state.config = config

    # Middleware (added last = outermost): CORS handles preflight, then PNA header, then Host guard.
    app.add_middleware(HostGuardMiddleware, port=config.port)
    app.add_middleware(PrivateNetworkMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.all_allowed_origins(),
        allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
        allow_headers=['authorization', 'content-type'],
        max_age=600,
    )

    app.include_router(routes_meta.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_events.router)
    app.include_router(routes_artifacts.router)

    if config.static_dir and os.path.isdir(config.static_dir):
        app.mount('/', StaticFiles(directory=config.static_dir, html=True), name='frontend')

    return app

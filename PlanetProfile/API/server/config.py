""" Server configuration: CLI/env parsing and defaults for the local backend. """
import os
import secrets
import argparse
import multiprocessing
from dataclasses import dataclass, field
from typing import List, Optional


def _default_workers():
    try:
        return max(1, (multiprocessing.cpu_count() or 2) - 2)
    except NotImplementedError:
        return 2


@dataclass
class ServerConfig:
    host: str = '127.0.0.1'                 # loopback only; never 0.0.0.0
    port: int = 8787
    workers: int = field(default_factory=_default_workers)
    data_dir: str = './mm-data'             # jobdirs live under <data_dir>/runs/<id>
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    allowed_origins: List[str] = field(default_factory=list)   # GitHub-Pages origin(s)
    static_dir: Optional[str] = None        # frontend bundle to serve same-origin at /
    job_timeout_s: float = 3600.0           # per-job wall-clock cap -> kill+respawn
    max_grid_cells: int = 4000              # reject explore grids larger than this
    max_body_bytes: int = 4_000_000         # request-body cap (corner-plot payloads are larger)
    max_infer_samples: int = 50000          # bound posterior-sample payload size
    jobdir_ttl_s: float = 24 * 3600.0       # reaper deletes jobdirs older than this

    @property
    def runs_dir(self):
        return os.path.abspath(os.path.join(self.data_dir, 'runs'))

    def loopback_origins(self):
        return {f'http://127.0.0.1:{self.port}', f'http://localhost:{self.port}',
                f'http://[::1]:{self.port}'}

    def all_allowed_origins(self):
        return sorted(set(self.allowed_origins) | self.loopback_origins())

    @classmethod
    def from_args(cls, argv=None):
        p = argparse.ArgumentParser(prog='python -m PlanetProfile.API.server',
                                    description='MoonMelodies local backend (FastAPI/uvicorn).')
        p.add_argument('--host', default=os.environ.get('MM_HOST', '127.0.0.1'))
        p.add_argument('--port', type=int, default=int(os.environ.get('MM_PORT', '8787')))
        p.add_argument('--workers', type=int, default=int(os.environ.get('MM_WORKERS', '0')),
                       help='warm workers (0 = auto: cpu-2)')
        p.add_argument('--data-dir', default=os.environ.get('MM_DATA_DIR', './mm-data'))
        p.add_argument('--token', default=os.environ.get('MM_TOKEN'),
                       help='session bearer token (default: random, printed at startup)')
        p.add_argument('--allowed-origin', action='append', default=[],
                       help='GitHub-Pages origin to allow via CORS (repeatable)')
        p.add_argument('--static-dir', default=os.environ.get('MM_STATIC_DIR'),
                       help='serve a static frontend bundle same-origin at /')
        p.add_argument('--job-timeout', type=float, default=float(os.environ.get('MM_JOB_TIMEOUT', '3600')))
        args = p.parse_args(argv)

        kwargs = dict(host=args.host, port=args.port, data_dir=args.data_dir,
                      allowed_origins=list(args.allowed_origin), static_dir=args.static_dir,
                      job_timeout_s=args.job_timeout)
        if args.workers > 0:
            kwargs['workers'] = args.workers
        if args.token:
            kwargs['token'] = args.token
        env_origins = os.environ.get('MM_ALLOWED_ORIGINS')
        if env_origins:
            kwargs['allowed_origins'] = kwargs['allowed_origins'] + [o.strip() for o in env_origins.split(',') if o.strip()]
        return cls(**kwargs)

""" Entrypoint: ``python -m PlanetProfile.API.server`` — configure and run uvicorn.

Must be started from a config root that has a seeded ``UserConfigs/`` (run
``python -m PlanetProfile.install`` first), so the worker imports don't block on the
import-time stdin prompt.
"""
import os
# The server process hosts torch for amortized inference (via PlanetProfile.API.inference ->
# sbi_runner). Allow the duplicate libomp load before torch is first imported. This is safe
# here: the uvicorn process never imports the forward interior engine (that lives in the
# separate ppworker subprocesses), so the libomp double-init hazard the CLAUDE.md flags for
# in-process dataset-generation + training does not arise on this path.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

import sys
import logging

from PlanetProfile.API.server.config import ServerConfig
from PlanetProfile.API.server.app import build_app


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn is not installed. Install the backend extra:  pip install -e '.[backend]'")

    cfg = ServerConfig.from_args(argv)
    app = build_app(cfg)

    print('\n' + '=' * 66, file=sys.stderr)
    print(f'  MoonMelodies backend  →  http://{cfg.host}:{cfg.port}', file=sys.stderr)
    print(f'  session token: {cfg.token}', file=sys.stderr)
    print(f'  workers: {cfg.workers}   data-dir: {cfg.data_dir}', file=sys.stderr)
    if cfg.allowed_origins:
        print(f'  CORS origins: {", ".join(cfg.allowed_origins)}', file=sys.stderr)
    if cfg.static_dir:
        print(f'  serving frontend from: {cfg.static_dir}', file=sys.stderr)
    print('=' * 66 + '\n', file=sys.stderr)

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level='warning')


if __name__ == '__main__':
    main()

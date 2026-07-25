""" MoonMelodies local FastAPI backend (Phase 4).

A loopback-only HTTP/JSON + SSE server that fronts the unchanged PlanetProfile engine
through a warm pool of ``ppworker`` subprocesses. It validates declarative JSON requests
(reusing the Phase 3 ``PlanetProfile.API`` package), schedules them onto the pool, streams
progress, and serves the resulting data + artifacts to a browser frontend.

Run it with::

    python -m PlanetProfile.API.server --workers 8 --allowed-origin https://<user>.github.io

See ``config.ServerConfig`` for all options. The app object is ``app.build_app(config)``.
"""
from PlanetProfile.API.server.config import ServerConfig
from PlanetProfile.API.server.app import build_app

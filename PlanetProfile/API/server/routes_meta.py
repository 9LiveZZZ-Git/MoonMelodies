""" Meta endpoints: /health (liveness), /bodies, /schema, /schema/{body}. """
import os
import importlib
import logging

from fastapi import APIRouter, Depends, Request, HTTPException

from PlanetProfile.API.server.security import require_token
from PlanetProfile.API import schema as schemamod, validate, mapper

router = APIRouter()
log = logging.getLogger('PlanetProfile.API.server')


def _version():
    try:
        from PlanetProfile.Utilities.PPversion import ppVerNum
        return str(ppVerNum)
    except Exception:
        return 'unknown'


def _perplex_ok():
    """ Best-effort check that the Perple_X EOS tables are available on disk. """
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_dir = os.path.normpath(os.path.join(here, '..', '..', 'Thermodynamics', 'EOStables', 'Perple_X'))
    candidates = [pkg_dir]
    try:
        import platformdirs
        candidates.append(os.path.join(platformdirs.user_cache_dir('PlanetProfile'), 'Perple_X'))
    except Exception:
        pass
    for d in candidates:
        if os.path.isdir(d) and any(f.endswith('.tab') for f in os.listdir(d)):
            return True
    return False


@router.get('/health')
async def health(request: Request):
    """ Liveness + readiness. Unauthenticated so the frontend can detect the backend. """
    app = request.app
    pool = getattr(app.state, 'pool', None)
    reg = getattr(app.state, 'registry', None)
    return {
        'status': 'ok',
        'version': _version(),
        'workersReady': pool.ready_count() if pool else 0,
        'workersTotal': pool.total_count() if pool else 0,
        'queueDepth': reg.queue.qsize() if reg else 0,
        'perplexCacheOK': _perplex_ok(),
    }


@router.get('/bodies', dependencies=[Depends(require_token)])
async def bodies():
    return {'bodies': [{'name': b, 'hasDefault': True} for b in sorted(validate.KNOWN_BODIES)]}


_SCHEMA_CACHE = None


@router.get('/schema', dependencies=[Depends(require_token)])
async def get_schema():
    # The payload is deterministic per install (structs + EOS-table listing); build it once.
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = schemamod.schema_payload()
    return _SCHEMA_CACHE


@router.get('/schema/{body}', dependencies=[Depends(require_token)])
async def schema_body(body: str):
    """ Default input values for a body, from the trusted Default/ PP module (a package
        file, NOT user input) via planet_to_spec — never by executing a user file. """
    if body not in validate.KNOWN_BODIES:
        raise HTTPException(status_code=404, detail=f'unknown body "{body}"')
    try:
        mod = importlib.import_module(f'PlanetProfile.Default.{body}.PP{body}')
        return {'body': body, 'defaults': mapper.planet_to_spec(mod.Planet)}
    except Exception as e:                                  # noqa: BLE001
        log.warning(f'no default spec for {body}: {e}')
        raise HTTPException(status_code=404, detail=f'no default configuration for "{body}"')

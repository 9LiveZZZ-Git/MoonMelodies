""" Run endpoints: submit, list, status, result, cancel. """
import os
import json
import logging

from fastapi import APIRouter, Depends, Request, HTTPException

from PlanetProfile.API.server.security import require_token
from PlanetProfile.API.server.registry import TERMINAL
from PlanetProfile.API import validate

router = APIRouter()
log = logging.getLogger('PlanetProfile.API.server')

_GRID_MODES = {'exploreogram', 'inductogram', 'montecarlo'}


@router.post('/runs', status_code=202, dependencies=[Depends(require_token)])
async def submit(request: Request):
    cfg = request.app.state.config
    raw = await request.body()
    if len(raw) > cfg.max_body_bytes:
        raise HTTPException(status_code=413, detail='request body too large')
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f'invalid JSON: {e}')

    errors = validate.validate_request(spec)
    # Resource cap: bound grid size before a worker is touched.
    mode = (spec.get('mode') or 'single').lower()
    if mode in _GRID_MODES:
        ex = spec.get('explore') or {}
        cells = int(ex.get('nx', 0) or 0) * int(ex.get('ny', 0) or 0)
        if cells > cfg.max_grid_cells:
            errors = list(errors) + [{'field': 'explore',
                                      'message': f'grid {cells} cells exceeds cap {cfg.max_grid_cells}'}]
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    reg = request.app.state.registry
    job = reg.create(spec)
    await reg.queue.put(job.id)
    return {'id': job.id, 'status': 'queued', 'mode': job.mode,
            'links': {'self': f'/runs/{job.id}', 'events': f'/runs/{job.id}/events',
                      'result': f'/runs/{job.id}/result', 'artifacts': f'/runs/{job.id}/artifacts'}}


@router.get('/runs', dependencies=[Depends(require_token)])
async def list_runs(request: Request):
    return {'runs': request.app.state.registry.list()}


@router.get('/runs/{jid}', dependencies=[Depends(require_token)])
async def get_run(jid: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')
    return job.public()


@router.get('/runs/{jid}/result', dependencies=[Depends(require_token)])
async def get_result(jid: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')
    if job.status not in TERMINAL:
        raise HTTPException(status_code=409, detail=f'run is {job.status}, not finished')
    path = os.path.join(job.jobdir, 'result.json')
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    # No result.json (failed/invalid before writing): return the status envelope.
    return {'meta': {'body': job.body, 'valid': job.status == 'succeeded', 'status': job.status},
            'error': job.error}


@router.delete('/runs/{jid}', dependencies=[Depends(require_token)])
async def cancel(jid: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')
    if job.status == 'queued':
        job.status = 'canceled'
        job.cancel.set()
    elif job.status == 'running':
        job.cancel.set()                 # _run_one races this and kills+respawns the worker
    # terminal jobs: nothing to cancel
    return {'id': jid, 'status': 'canceling' if job.status == 'running' else job.status}

""" Amortized Bayesian inference endpoints.

Unlike /runs (a queued forward-model job with SSE progress), inference is a plain
request/response: the amortized SBI posterior samples in well under a second, so POST /infer
loads/uses the flow in a worker thread and returns the corner-plot payload directly.

  GET  /infer/artifacts        -> deployable slots present on disk (selector + form defaults)
  GET  /infer/artifacts/{id}   -> full metadata for one slot (fiducials, sigmas, bounds, guards)
  POST /infer                  -> {artifact, x_obs:{name:val}, nSamples, seed} -> samples + summary
"""
import json
import asyncio
import logging

from fastapi import APIRouter, Depends, Request, HTTPException

from PlanetProfile.API.server.security import require_token
from PlanetProfile.API import inference

router = APIRouter()
log = logging.getLogger('PlanetProfile.API.server')


@router.get('/infer/artifacts', dependencies=[Depends(require_token)])
async def infer_artifacts():
    # First call pays the one-time torch import + per-slot deserialize; run off the loop.
    arts = await asyncio.to_thread(inference.list_artifacts)
    return {'artifacts': arts}


@router.get('/infer/artifacts/{slot_id}', dependencies=[Depends(require_token)])
async def infer_artifact(slot_id: str):
    art = await asyncio.to_thread(inference.get_artifact, slot_id)
    if art is None:
        raise HTTPException(status_code=404, detail=f'unknown or unavailable artifact "{slot_id}"')
    return art


@router.post('/infer', dependencies=[Depends(require_token)])
async def infer(request: Request):
    cfg = request.app.state.config
    clen = request.headers.get('content-length')
    if clen and clen.isdigit() and int(clen) > cfg.max_body_bytes:
        raise HTTPException(status_code=413, detail='request body too large')
    raw = await request.body()
    if len(raw) > cfg.max_body_bytes:
        raise HTTPException(status_code=413, detail='request body too large')
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f'invalid JSON: {e}')
    if not isinstance(body, dict):
        raise HTTPException(status_code=422,
                            detail=[{'field': '', 'message': 'request must be a JSON object'}])

    slotId = body.get('artifact')
    xObs = body.get('x_obs') or body.get('xObs') or {}
    nSamples = body.get('nSamples', body.get('n_samples', 10000))
    seed = body.get('seed')

    errors = []
    if not isinstance(slotId, str) or not slotId:
        errors.append({'field': 'artifact', 'message': 'a slot id string is required'})
    if not isinstance(xObs, dict):
        errors.append({'field': 'x_obs', 'message': 'expected an object of {name: value}'})
    if isinstance(nSamples, bool) or not isinstance(nSamples, int) or nSamples < 1:
        errors.append({'field': 'nSamples', 'message': 'must be a positive integer'})
    elif nSamples > cfg.max_infer_samples:
        errors.append({'field': 'nSamples',
                       'message': f'{nSamples} exceeds cap {cfg.max_infer_samples}'})
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        errors.append({'field': 'seed', 'message': 'must be an integer or omitted'})
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    try:
        result = await asyncio.to_thread(inference.run_infer, slotId, xObs, nSamples, seed)
    except inference.InferError as e:
        raise HTTPException(status_code=422, detail=e.errors)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:                                # noqa: BLE001
        log.exception('inference failed')
        raise HTTPException(status_code=500, detail=f'inference failed: {e}')
    return result

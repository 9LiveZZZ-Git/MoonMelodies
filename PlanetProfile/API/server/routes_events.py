""" SSE progress stream: GET /runs/{id}/events (Server-Sent Events). """
import json
import asyncio

from fastapi import APIRouter, Depends, Request, HTTPException
from starlette.responses import StreamingResponse

from PlanetProfile.API.server.security import require_token

router = APIRouter()


@router.get('/runs/{jid}/events', dependencies=[Depends(require_token)])
async def events(jid: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')

    async def gen():
        q = job.subscribe()          # replays the event log so a late subscriber isn't behind
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'           # SSE comment heartbeat
                    if job.done.is_set():
                        break
                    continue
                yield f'data: {json.dumps(ev)}\n\n'
                if ev.get('terminal'):
                    break
        finally:
            job.unsubscribe(q)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                                      'Connection': 'keep-alive'})

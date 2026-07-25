""" Artifact endpoints: enumerate and download on-disk artifacts, manifest-guarded. """
import os

from fastapi import APIRouter, Depends, Request, HTTPException
from starlette.responses import FileResponse

from PlanetProfile.API.server.security import require_token
from PlanetProfile.API import results

router = APIRouter()


@router.get('/runs/{jid}/artifacts', dependencies=[Depends(require_token)])
async def list_artifacts(jid: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')
    manifest = results.build_manifest(job.jobdir, hrefBase=f'/runs/{jid}/artifacts')
    return {'artifacts': manifest}


@router.get('/runs/{jid}/artifacts/{name:path}', dependencies=[Depends(require_token)])
async def get_artifact(jid: str, name: str, request: Request):
    job = request.app.state.registry.get(jid)
    if job is None:
        raise HTTPException(status_code=404, detail='no such run')
    # Path-safety IS the allowlist: the resolved path must be a real file strictly inside
    # the jobdir. This blocks traversal, needs no jobdir walk, and (unlike the worker's
    # pre-write manifest) correctly serves result.json and anything written after it.
    jobdir = os.path.abspath(job.jobdir)
    path = os.path.abspath(os.path.join(jobdir, name))
    if not path.startswith(jobdir + os.sep):
        raise HTTPException(status_code=403, detail='path escapes jobdir')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='no such artifact')
    return FileResponse(path, filename=os.path.basename(name))

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
    # Serve ONLY names present in the manifest — no client-supplied paths, no traversal.
    names = {m['name'] for m in results.build_manifest(job.jobdir)}
    if name not in names:
        raise HTTPException(status_code=404, detail='no such artifact')
    path = os.path.abspath(os.path.join(job.jobdir, name))
    if not path.startswith(os.path.abspath(job.jobdir) + os.sep):
        raise HTTPException(status_code=403, detail='path escapes jobdir')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='artifact missing on disk')
    return FileResponse(path, filename=os.path.basename(name))

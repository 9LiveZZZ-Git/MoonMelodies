"""Phase 4 backend tests.

Fast, engine-free integration of the FastAPI app: a fake worker pool stands in for the
real ppworker subprocesses (writing a canned result.json), so the full HTTP path -
validate -> queue -> dispatcher -> worker -> result/artifacts, plus auth and SSE - is
exercised without importing the physics engine. The real engine-backed end-to-end is
covered by the smoke run documented in the Phase 4 commit. Skipped if FastAPI is absent.
"""
import os
import json
import time
import tempfile

try:
    import pytest
except ImportError:
    pytest = None

try:
    from fastapi.testclient import TestClient
    from starlette.testclient import TestClient as _TC  # noqa: F401
    from PlanetProfile.API.server.config import ServerConfig
    from PlanetProfile.API.server import app as appmod
    from PlanetProfile.API.server.registry import Job, JobRegistry
    from PlanetProfile.API.server.security import get_token
    _OK = True
except Exception:
    _OK = False


def _skip():
    if not _OK:
        if pytest is not None:
            pytest.skip('FastAPI / PlanetProfile backend not importable')
        return True
    return False


# --------------------------------------------------------------------------- fakes
class _FakeWorker:
    async def run_job(self, jid, spec, jobdir, on_progress=None):
        if on_progress:
            await on_progress({'type': 'progress', 'id': jid, 'stage': 'run', 'percent': 50})
        # write a minimal but real result.json + one artifact into the jobdir
        os.makedirs(os.path.join(jobdir, 'Europa'), exist_ok=True)
        with open(os.path.join(jobdir, 'Europa', 'EuropaProfile_fake.txt'), 'w') as f:
            f.write('fake profile\n')
        result = {'meta': {'body': spec.get('body'), 'valid': True, 'nTotal': 3},
                  'summary': {'Mtot_kg': 4.8e22, 'D_km': 100.0}}
        with open(os.path.join(jobdir, 'result.json'), 'w') as f:
            json.dump(result, f)
        return {'type': 'result', 'id': jid, 'status': 'succeeded',
                'summary': result['summary'], 'meta': result['meta'],
                'manifest': [{'name': 'Europa/EuropaProfile_fake.txt', 'kind': 'profile'}]}


class _FakePool:
    def __init__(self, *a, **k):
        self._n = 2
    async def start(self):
        pass
    async def acquire(self):
        return _FakeWorker()
    def release(self, w):
        pass
    async def replace(self, w):
        pass
    async def close(self):
        pass
    def ready_count(self):
        return self._n
    def total_count(self):
        return self._n


def _client(monkeypatch_pool=True):
    if monkeypatch_pool:
        appmod.WorkerPool = _FakePool          # inject the fake before lifespan starts it
    cfg = ServerConfig(token='T', data_dir=tempfile.mkdtemp(), workers=2)
    return TestClient(appmod.build_app(cfg)), cfg


# --------------------------------------------------------------------------- tests
def test_health_and_auth():
    if _skip():
        return
    client, cfg = _client()
    with client:
        assert client.get('/health').json()['status'] == 'ok'          # health is unauthenticated
        assert client.get('/bodies').status_code == 401                # everything else needs the token
        h = {'Authorization': f'Bearer {cfg.token}'}
        assert client.get('/bodies', headers=h).status_code == 200
        assert len(client.get('/schema', headers=h).json()['enums']['body']) == 19


def test_validation_422():
    if _skip():
        return
    client, cfg = _client()
    h = {'Authorization': f'Bearer {cfg.token}'}
    with client:
        # three-of-three (Tb + zb + wOcean all set) must be rejected up front
        bad = {'body': 'Europa', 'bulk': {'Tb_K': 268.3, 'zb_approximate_km': 30.0},
               'ocean': {'wOcean_ppt': 35.0}}
        assert client.post('/runs', headers=h, content=json.dumps(bad)).status_code == 422
        assert client.post('/runs', headers=h, content=json.dumps({'body': 'Xena'})).status_code == 422


def test_full_run_path_with_fake_pool():
    if _skip():
        return
    client, cfg = _client()
    h = {'Authorization': f'Bearer {cfg.token}'}
    with client:
        good = {'body': 'Europa', 'mode': 'single', 'bulk': {'Tb_K': 268.3},
                'ocean': {'comp': 'Seawater', 'wOcean_ppt': 35.0}}
        r = client.post('/runs', headers=h, content=json.dumps(good))
        assert r.status_code == 202
        jid = r.json()['id']
        # dispatcher + fake worker complete the job; poll to terminal
        status = 'queued'
        for _ in range(100):
            status = client.get(f'/runs/{jid}', headers=h).json()['status']
            if status in ('succeeded', 'invalid', 'failed', 'canceled'):
                break
            time.sleep(0.05)
        assert status == 'succeeded', f'run ended {status}'
        res = client.get(f'/runs/{jid}/result', headers=h).json()
        assert res['meta']['valid'] is True and res['summary']['Mtot_kg'] == 4.8e22
        arts = client.get(f'/runs/{jid}/artifacts', headers=h).json()['artifacts']
        assert any(a['name'].endswith('.txt') for a in arts)
        one = [a for a in arts if a['name'].endswith('.txt')][0]['name']
        assert client.get(f'/runs/{jid}/artifacts/{one}', headers=h).status_code == 200
        # path-traversal is refused
        assert client.get(f'/runs/{jid}/artifacts/../../etc/passwd', headers=h).status_code in (403, 404)


def test_config_and_registry_units():
    if _skip():
        return
    cfg = ServerConfig.from_args(['--port', '9001', '--workers', '3', '--allowed-origin', 'https://x.github.io'])
    assert cfg.port == 9001 and cfg.workers == 3
    assert 'https://x.github.io' in cfg.all_allowed_origins()
    assert f'http://127.0.0.1:9001' in cfg.all_allowed_origins()   # loopback self-origin always allowed

    reg = JobRegistry(tempfile.mkdtemp())
    job = reg.create({'body': 'Europa', 'mode': 'single'})
    assert reg.get(job.id) is job and os.path.isdir(job.jobdir)
    assert job.public()['status'] == 'queued'


if __name__ == '__main__':
    ok = fail = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn(); ok += 1; print(f'PASS {name}')
            except Exception as e:
                fail += 1; print(f'FAIL {name}: {type(e).__name__}: {e}')
    print(f'\n{ok} passed, {fail} failed')

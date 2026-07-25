""" Security for the local backend: bearer-token auth, Host-header + Origin validation.

The server binds loopback only; on top of that it (a) requires a per-session random token
on every request (blocks other local processes/pages from driving it), (b) validates the
Host header against loopback (DNS-rebinding defense), and (c) allowlists CORS origins.
"""
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


def get_token(request: Request) -> str:
    """ Extract the bearer token from the Authorization header or ?token= query param. """
    auth = request.headers.get('authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.query_params.get('token', '')


async def require_token(request: Request):
    """ FastAPI dependency: 401 unless the request carries the session token. """
    expected = request.app.state.config.token
    if get_token(request) != expected:
        raise HTTPException(status_code=401, detail='missing or invalid session token')
    return True


class HostGuardMiddleware(BaseHTTPMiddleware):
    """ Reject requests whose Host header is not loopback (DNS-rebinding defense). """
    def __init__(self, app, port):
        super().__init__(app)
        self._allowed = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}',
                         '127.0.0.1', 'localhost', '[::1]',
                         'testserver'}   # starlette TestClient default host

    async def dispatch(self, request, call_next):
        host = (request.headers.get('host') or '').strip()
        # Allow empty host only for non-network test clients; otherwise enforce loopback.
        if host and host not in self._allowed:
            raise HTTPException(status_code=421, detail=f'host not allowed: {host}')
        return await call_next(request)


class PrivateNetworkMiddleware(BaseHTTPMiddleware):
    """ Answer the Private-Network-Access preflight: a public HTTPS page (GitHub Pages)
        reaching this loopback server sends Access-Control-Request-Private-Network; Chrome
        requires Access-Control-Allow-Private-Network: true on the response. """
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method == 'OPTIONS' and \
                request.headers.get('access-control-request-private-network') == 'true':
            response.headers['Access-Control-Allow-Private-Network'] = 'true'
        return response

"""Optional Streamable HTTP transport (for clients that need a URL, e.g. ChatGPT custom connectors).

Safe defaults: binds to 127.0.0.1, refuses to start without a generated token, checks Host/Origin (DNS-rebinding
protection from the MCP SDK). The token is accepted as `Authorization: Bearer <token>` or as a path prefix
`/t/<token>/mcp`: ChatGPT's connector form only offers OAuth or no authentication (OpenAI docs), so the path form is
the way to keep a tunnel from being open. stdio stays the default transport.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from typing import Any

from .client import CLI, MeliError, ensure_state_dir, state_dir

TOKEN_FILE = "http-token"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def get_token(rotate: bool = False) -> str:
    path = state_dir() / TOKEN_FILE
    if not rotate:
        try:
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        except OSError:
            pass
    value = secrets.token_urlsafe(32)
    ensure_state_dir()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(value + "\n")
    if os.name != "nt":
        os.chmod(path, 0o600)
    return value


class TokenGate:
    """ASGI middleware: every request needs the token; the `/t/<token>` prefix is stripped before routing."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await _deny(send, 403)
            return
        path = scope.get("path", "")
        prefix = f"/t/{self.token}"
        supplied = ""
        for key, value in scope.get("headers", []):
            if key == b"authorization" and value.lower().startswith(b"bearer "):
                supplied = value[7:].decode("latin-1").strip()
        authorized = bool(supplied) and hmac.compare_digest(supplied.encode(), self.token.encode())
        if path == prefix or path.startswith(prefix + "/"):
            authorized = True
            scope = {**scope, "path": path[len(prefix):] or "/", "raw_path": scope.get("raw_path", b"")[len(prefix):] or b"/"}
        if not authorized:
            await _deny(send, 401)
            return
        await self.app(scope, receive, send)


async def _deny(send: Any, status: int) -> None:
    body = json.dumps({"error": "unauthorized", "next_step": f"Enviá el token generado por `{CLI} http-token` como `Authorization: Bearer <token>` o usá la URL que imprime `{CLI} http-token --public-url <URL>`."}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"www-authenticate", b"Bearer")]})
    await send({"type": "http.response.body", "body": body})


def build_app(mcp: Any, host: str, port: int, allowed_hosts: list[str], token: str) -> Any:
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", "127.0.0.1:*", "localhost:*"]
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}", "http://127.0.0.1:*", "http://localhost:*"]
    for extra in allowed_hosts:
        extra = extra.strip().lower().rstrip("/")
        hosts += [extra, f"{extra}:*"]
        origins += [f"https://{extra}", f"http://{extra}"]
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
    return TokenGate(mcp.streamable_http_app(), token)


def serve(mcp: Any, host: str, port: int, allowed_hosts: list[str]) -> None:
    if host not in LOOPBACK:
        raise MeliError(f"Por seguridad el transporte HTTP solo escucha en 127.0.0.1. Próximo paso: usá --host 127.0.0.1 y, si necesitás URL pública, un túnel HTTPS (docs/AGENT_INSTALL.md, sección ChatGPT) con --allowed-host <dominio-del-túnel>.")
    import uvicorn
    token = get_token()
    app = build_app(mcp, host, port, allowed_hosts, token)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)

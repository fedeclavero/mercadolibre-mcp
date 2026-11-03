import json
import os

import pytest
from starlette.testclient import TestClient

from meli_mcp import cli, httpserve, server
from meli_mcp.client import MeliError

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_token_is_generated_private_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    first = httpserve.get_token()
    assert len(first) >= 40 and httpserve.get_token() == first
    if os.name != "nt":
        assert (tmp_path / "http-token").stat().st_mode & 0o777 == 0o600
    assert httpserve.get_token(rotate=True) != first


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    token = httpserve.get_token()
    monkeypatch.setattr(server.mcp, "_session_manager", None)   # the SDK allows one run() per manager; each test builds its own
    app = httpserve.build_app(server.mcp, "127.0.0.1", 8765, ["demo.trycloudflare.com"], token)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.token = token
        yield c


def test_http_requires_the_generated_token(client):
    assert client.post("/mcp", headers=HEADERS, json=INITIALIZE).status_code == 401
    assert client.post("/mcp", headers={**HEADERS, "Authorization": "Bearer nope"}, json=INITIALIZE).status_code == 401
    assert client.post("/t/otro/mcp", headers=HEADERS, json=INITIALIZE).status_code in (401, 404)
    body = client.post("/mcp", headers=HEADERS, json=INITIALIZE).json()
    assert body["error"] == "unauthorized" and "http-token" in body["next_step"]


def test_bearer_header_and_path_token_both_reach_the_mcp_endpoint(client):
    with_header = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {client.token}"}, json=INITIALIZE)
    assert with_header.status_code == 200 and "mercadolibre_mcp" in with_header.text
    with_path = client.post(f"/t/{client.token}/mcp", headers=HEADERS, json=INITIALIZE)
    assert with_path.status_code == 200 and "mercadolibre_mcp" in with_path.text


def test_dns_rebinding_protection_rejects_foreign_hosts(client):
    reply = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {client.token}", "Host": "evil.example"}, json=INITIALIZE)
    assert reply.status_code in (400, 421)
    ok = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {client.token}", "Host": "demo.trycloudflare.com"}, json=INITIALIZE)
    assert ok.status_code == 200


def test_http_refuses_non_loopback_bind():
    with pytest.raises(MeliError, match="127.0.0.1"):
        httpserve.serve(server.mcp, "0.0.0.0", 8765, [])


def test_http_token_command_prints_the_connector_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    cli.main(["http-token", "--public-url", "https://demo.trycloudflare.com/", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["connector_url"] == f"https://demo.trycloudflare.com/t/{data['token']}/mcp"

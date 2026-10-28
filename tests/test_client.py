import os
import concurrent.futures
import time
from urllib.parse import parse_qs
import httpx
import pytest
from meli_mcp.client import MeliClient, MeliError, checked_path, load_private, write_private

@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    write_private(tmp_path / "config.json", {"client_id": "id", "client_secret": "secret"})
    write_private(tmp_path / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": 0})
    return tmp_path

@pytest.mark.parametrize("path", ["https://evil.com/a", "//evil.com/a", "/a?x=1", "/../a", "/%2e%2e/a", "/%252e%252e/a", "/a\\\\host", "/oauth/token", "/%2f%2fevil.com"])
def test_reject_invalid_origins_and_traversal(path):
    with pytest.raises(MeliError): checked_path(path)

def test_rotate_once_under_concurrency(state):
    count = 0
    def handler(request):
        nonlocal count
        assert request.url.host == "api.mercadolibre.com"
        if request.url.path == "/oauth/token":
            count += 1
            assert parse_qs(request.content.decode())["refresh_token"] == ["refresh-old"]
            time.sleep(0.03)
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer new"
        return httpx.Response(200, json={"ok": True})
    c = MeliClient(httpx.MockTransport(handler))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: c.request("GET", "/users/me"), range(8))) == [{"ok": True}] * 8
    assert count == 1
    assert load_private("token.json")["refresh_token"] == "refresh-new"
    if os.name != "nt":
        assert (state / "token.json").stat().st_mode & 0o777 == 0o600
    assert "refresh-new" not in (state / "audit.jsonl").read_text()

def test_get_401_refresh_once_and_mutation_never_retried(state):
    write_private(state / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": time.time()+3600})
    calls = []
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/oauth/token": return httpx.Response(200, json={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 3600})
        if req.headers["authorization"] == "Bearer old": return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"ok": True})
    c = MeliClient(httpx.MockTransport(handler))
    assert c.request("GET", "/users/me") == {"ok": True}
    assert calls == [("GET", "/users/me"), ("POST", "/oauth/token"), ("GET", "/users/me")]
    write_private(state / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": time.time()+3600})
    calls.clear()
    with pytest.raises(MeliError, match="401"): c.request("PUT", "/items/MLA1", body={"price": 1})
    assert calls == [("PUT", "/items/MLA1")]

def test_corrupt_token_fails_cleanly(state):
    (state / "token.json").write_text("not json")
    with pytest.raises(MeliError, match="No se pudo leer"):
        MeliClient().access_token()

def test_version_and_no_redirect(state):
    write_private(state / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": time.time()+3600})
    def handler(req):
        assert req.headers["x-version"] == "4"
        return httpx.Response(200, headers={"x-version": "5"}, json={"quantity": 2})
    c = MeliClient(httpx.MockTransport(handler))
    assert c.request("PUT", "/stock/a", body={"quantity": 2}, extra_headers={"x-version": "4"}) == {"data": {"quantity": 2}, "response_headers": {"x-version": "5"}}
    with pytest.raises(MeliError): c.request("GET", "/a", extra_headers={"authorization": "attack"})

def test_redirect_does_not_send_bearer_to_another_host(state):
    write_private(state / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": time.time()+3600})
    requests=[]
    def handler(req):
        requests.append(str(req.url))
        return httpx.Response(302, headers={"Location": "https://evil.com/"})
    with pytest.raises(MeliError, match="302"): MeliClient(httpx.MockTransport(handler)).request("GET", "/users/me")
    assert len(requests) == 1

def test_audit_failure_does_not_hide_successful_write(state, monkeypatch):
    import meli_mcp.client as mod
    write_private(state / "token.json", {"access_token": "old", "refresh_token": "refresh-old", "expires_at": time.time()+3600})
    monkeypatch.setattr(mod, "audit", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    c = MeliClient(httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "MLA1", "price": 42})))
    result = c.request("PUT", "/items/MLA1", body={"price": 42})
    assert result["data"]["price"] == 42
    assert "auditoría" in result["warning"]

def test_malformed_token_expiration_fails_cleanly(state):
    write_private(state / "token.json", {"access_token": "old", "expires_at": "invalid"})
    with pytest.raises(MeliError, match="expiración inválida"):
        MeliClient().access_token()

def test_errors_never_return_private_body(state):
    write_private(state / "token.json", {"access_token": "old", "expires_at": time.time()+3600})
    c = MeliClient(httpx.MockTransport(lambda req: httpx.Response(403, json={"error": "forbidden", "message": "sensitive customer data"})))
    with pytest.raises(MeliError) as exc:
        c.request("GET", "/orders/1")
    assert "403" in str(exc.value) and "sensitive" not in str(exc.value)

@pytest.mark.parametrize("status", [403, 429, 500])
def test_write_errors_never_retry(state, status):
    write_private(state / "token.json", {"access_token": "old", "expires_at": time.time()+3600})
    calls = []
    def handler(req):
        calls.append(req.method)
        return httpx.Response(status, json={"error": "error"})
    with pytest.raises(MeliError, match=str(status)):
        MeliClient(httpx.MockTransport(handler)).request("PUT", "/items/MLA1", body={"price": 10})
    assert calls == ["PUT"]


def test_refresh_is_serialized_across_processes(state):
    import subprocess
    import sys
    code = r'''
import os, time
from pathlib import Path
import httpx
from meli_mcp.client import MeliClient
log = Path(os.environ["MELI_MCP_STATE_DIR"]) / "refresh-count.txt"
def fake(req):
    with log.open("a", encoding="utf-8") as f: f.write("refresh\n")
    time.sleep(0.2)
    return httpx.Response(200, json={"access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600})
print(MeliClient(httpx.MockTransport(fake)).access_token())
'''
    env = os.environ.copy()
    env["MELI_MCP_STATE_DIR"] = str(state)
    processes = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err
        assert out.strip() == "new"
    assert (state / "refresh-count.txt").read_text().splitlines() == ["refresh"]
    assert load_private("token.json")["refresh_token"] == "new-refresh"

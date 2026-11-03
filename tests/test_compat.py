"""State written by earlier releases (same file names and formats) must keep working untouched."""
import asyncio
import hashlib
import os
import sqlite3
import sys
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from meli_mcp import business, server
from meli_mcp.client import MeliClient, load_private

LEGACY_CONFIG = '{"client_id": "123456", "client_secret": "legacy-secret", "redirect_uri": "https://owner.example/callback", "country": "ar", "pkce": true}'


def legacy_state(path, expired=False):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(LEGACY_CONFIG)
    token = {"access_token": "APP_USR-legacy", "token_type": "bearer", "expires_in": 21600, "scope": "offline_access read write", "user_id": 77, "refresh_token": "TG-legacy",
             "expires_at": 0 if expired else time.time() + 3600}
    import json
    (path / "token.json").write_text(json.dumps(token))
    db = sqlite3.connect(path / "business.sqlite3")
    db.executescript("""CREATE TABLE product_rules (seller_id INTEGER NOT NULL, item_id TEXT NOT NULL, currency TEXT NOT NULL, minimum_advertised_price TEXT NOT NULL, max_discount_pct TEXT NOT NULL, cost TEXT, notes TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(seller_id,item_id));
                       CREATE TABLE policies (seller_id INTEGER NOT NULL, topic TEXT NOT NULL, rule_text TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(seller_id,topic));
                       INSERT INTO product_rules VALUES (77,'MLA1','ARS','500','30',NULL,'nota',1700000000);
                       INSERT INTO policies VALUES (77,'garantia','30 días',1700000000);""")
    db.commit(); db.close()
    ledger = sqlite3.connect(path / "question-attempts.sqlite3")
    ledger.execute("CREATE TABLE attempts (question_id INTEGER PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    ledger.execute("INSERT INTO attempts VALUES (9, 'sent', 1700000000)")
    ledger.commit(); ledger.close()


def digest(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.iterdir())}


def test_legacy_business_memory_and_ledger_are_read_as_is(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    legacy_state(tmp_path)
    memory = business.list_memory(77)
    assert memory["product_rules"][0]["minimum_advertised_price"] == "500" and memory["policies"][0]["rule_text"] == "30 días"
    from meli_mcp.ledger import get_attempt
    assert get_attempt(9) == "sent"


def test_legacy_token_is_used_without_reauthorizing(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    legacy_state(tmp_path)
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert request.headers["authorization"] == "Bearer APP_USR-legacy"
        return httpx.Response(200, json={"id": 77, "nickname": "VENDEDOR"})
    assert MeliClient(httpx.MockTransport(handler)).request("GET", "/users/me")["nickname"] == "VENDEDOR"
    assert calls == ["/users/me"]


def test_legacy_expired_token_refreshes_with_legacy_config_and_keeps_its_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    legacy_state(tmp_path, expired=True)
    from urllib.parse import parse_qs
    seen = []
    def handler(request):
        if request.url.path == "/oauth/token":
            seen.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "TG-new", "expires_in": 21600, "scope": "offline_access read write"})
        return httpx.Response(200, json={"ok": True})
    assert MeliClient(httpx.MockTransport(handler)).request("GET", "/users/me") == {"ok": True}
    assert seen == [{"grant_type": ["refresh_token"], "client_id": ["123456"], "client_secret": ["legacy-secret"], "refresh_token": ["TG-legacy"]}]
    assert load_private("token.json")["refresh_token"] == "TG-new"
    assert load_private("config.json")["client_secret"] == "legacy-secret"      # config is never rewritten by a refresh


def test_legacy_config_means_full_read_write_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("MELI_MCP_MODE", raising=False)
    legacy_state(tmp_path)
    from meli_mcp.client import access_mode
    assert access_mode() == "full"


def test_serve_with_legacy_registration_only_reads_the_state(tmp_path):
    """`mercadolibre-mcp serve` with no new flags: handshake, tools and a local tool call leave every state file unchanged."""
    state = tmp_path / "estado"
    legacy_state(state)
    before = digest(state)
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-c", "import sys; from meli_mcp.cli import main; sys.argv=['mercadolibre-mcp','serve']; main()"], env={**os.environ, "MELI_MCP_STATE_DIR": str(state)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"meli_account", "meli_list_items", "meli_update_item", "meli_api_request", "meli_set_item_pictures", "meli_answer_question", "meli_apply_deal_of_day"} <= names
                first = await session.call_tool("meli_start_here", {})
                assert not first.isError
                return init.serverInfo.version
    from meli_mcp import __version__
    assert asyncio.run(run()) == __version__
    assert digest(state) == before


def test_existing_tool_signatures_are_unchanged():
    import inspect
    expected = {
        "meli_create_item": ["data", "catalog_confirmed"], "meli_update_item": ["item_id", "changes", "catalog_confirmed"], "meli_set_item_pictures": ["item_id", "picture_urls"],
        "meli_list_items": ["seller_id", "limit", "offset", "status", "scroll_id", "scan"], "meli_api_request": ["method", "path", "query", "body", "headers", "catalog_confirmed", "explicitly_requested"],
        "meli_answer_question": ["question_id", "text", "explicitly_requested"], "meli_apply_deal_of_day": ["item_id", "deal_price", "explicitly_requested"],
        "meli_search_orders": ["seller_id", "limit", "offset", "sort"], "meli_unanswered_questions": ["seller_id", "limit", "offset"],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(server, name)).parameters) == params, name

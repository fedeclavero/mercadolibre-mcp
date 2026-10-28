import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_mcp_handshake_and_tools():
    async def run():
        p = StdioServerParameters(command="mercadolibre-mcp", args=["serve"])
        async with stdio_client(p) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert {"meli_account", "meli_list_items", "meli_update_item", "meli_api_request", "meli_set_item_pictures"} <= names
                assert len(names) >= 16
    asyncio.run(run())


def test_stdio_hides_http_query_from_diagnostic_logs():
    import sys
    import tempfile
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    code = r'''
import sys, httpx
from meli_mcp.client import MeliClient
MeliClient.access_token = lambda self, stale_token=None: "dummy-access"
MeliClient._client = lambda self: httpx.Client(base_url="https://api.mercadolibre.com", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})))
from meli_mcp.cli import main
sys.argv = ["mercadolibre-mcp", "serve"]
main()
'''
    async def run():
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as logs:
            params = StdioServerParameters(command=sys.executable, args=["-c", code])
            async with stdio_client(params, errlog=logs) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("meli_api_request", {"method": "GET", "path": "/items/search", "query": {"q": "private-query-marker"}})
                    assert not result.isError
            logs.seek(0)
            assert "private-query-marker" not in logs.read()
    asyncio.run(run())


def test_answer_receipt_survives_stdio_restart(tmp_path):
    import os
    import sys
    code = r'''
import sys, httpx
from meli_mcp.client import MeliClient
MeliClient.access_token = lambda self, stale_token=None: "dummy-access"
def handle(request):
    if request.url.path == "/users/me": return httpx.Response(200, json={"id": 123})
    if request.method == "GET": return httpx.Response(200, json={"id": 456, "seller_id": 123, "status": "UNANSWERED"})
    return httpx.Response(200, json={"ok": True})
MeliClient._client = lambda self: httpx.Client(base_url="https://api.mercadolibre.com", transport=httpx.MockTransport(handle))
from meli_mcp.cli import main
sys.argv = ["mercadolibre-mcp", "serve"]
main()
'''
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-c", code], env={**os.environ, "MELI_MCP_STATE_DIR": str(tmp_path)})
        for attempt in range(2):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("meli_answer_question", {"question_id": 456, "text": "Hola", "explicitly_requested": True})
                    assert bool(result.isError) == bool(attempt)
    asyncio.run(run())

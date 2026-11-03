import asyncio
import inspect
import struct
import sys
import time
import zlib

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from meli_mcp import images, recipes, server
from meli_mcp.client import MeliClient, MeliError, write_private


def png(width=1200, height=1200):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * (width * 3) for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg(width=800, height=600):
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + b"\xff\xd9"


class Api:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def request(self, method, path, query=None, body=None, extra_headers=None, files=None):
        self.calls.append({"method": method, "path": path, "query": query, "body": body, "files": files})
        for key, value in self.responses.items():
            if path == key or path.startswith(key):
                return value(query) if callable(value) else value
        raise AssertionError(f"llamada inesperada {method} {path}")


# ------------------------------------------------------------------ catalog / descriptions / recipes

def registered():
    return {t.name: t for t in server.mcp._tool_manager.list_tools()}


def test_catalog_covers_exactly_the_registered_tools():
    assert set(recipes.TOOL_CATALOG) == set(registered())
    assert len(registered()) == 48


def test_every_description_leads_with_the_use_this_tool_instruction():
    starts = ("USÁ ESTA TOOL PARA", "LLAMÁ ESTA PRIMERO", "ÚLTIMO RECURSO: USÁ ESTA TOOL PARA")
    for name, tool in registered().items():
        assert tool.description.startswith(starts), name
        assert len(tool.description) < 900, name


def test_recipes_only_reference_real_tools_and_real_arguments():
    tools = registered()
    for rid, recipe in recipes.RECIPES.items():
        for step in recipe["steps"]:
            name = step["tool"]
            if name.startswith("("):
                continue
            assert name in tools, (rid, name)
            params = set(inspect.signature(getattr(server, name)).parameters)
            assert set(step["args"]) <= params, (rid, name, set(step["args"]) - params)


def test_new_tools_do_not_write_to_the_account_except_picture_upload():
    new = {"meli_start_here", "meli_find_tool", "meli_get_recipe", "meli_status", "meli_my_items", "meli_combo_plan", "meli_upload_picture"}
    for name in new - {"meli_upload_picture"}:
        assert registered()[name].annotations.readOnlyHint is True, name
    assert registered()["meli_upload_picture"].annotations.readOnlyHint is False
    assert registered()["meli_upload_picture"].annotations.destructiveHint is False


def test_instructions_are_imperative_and_ban_the_browser():
    text = server.INSTRUCTIONS
    for needle in ("SIEMPRE usá las tools", "NO uses navegador", "agent-browser", "meli_start_here", "meli_find_tool", "meli_api_request", "Próximo paso", "meli_guia_indice", "combos"):
        assert needle in text, needle


@pytest.mark.parametrize("query,tool,recipe", [
    ("armar combos de mis productos con imagen", "meli_combo_plan", "combos"),
    ("quiero hacer un pack de 3 publicaciones", "meli_combo_plan", "combos"),
    ("subir una foto nueva a una publicación", "meli_upload_picture", "fotos"),
    ("bajar el precio de muchas publicaciones", "meli_update_item", "precios-masivos"),
    ("qué pedidos tengo para despachar", "meli_search_orders", "pedidos"),
    ("responder las preguntas de los compradores", "meli_unanswered_questions", "preguntas"),
    ("mejorar el título y la descripción", "meli_update_description", "titulos-descripciones"),
])
def test_find_tool_points_small_models_to_the_right_tool_and_recipe(query, tool, recipe):
    found = recipes.find(query)
    assert tool in [t["tool"] for t in found["tools"]], found
    assert recipe in [r["recipe"] for r in found["recipes"]], found


def test_find_tool_without_matches_never_suggests_the_browser():
    found = recipes.find("xyzzy qwerty")
    assert "meli_api_request" in found["nota"] and "NO uses el navegador" in found["nota"]


def test_start_here_lists_rules_recipes_and_all_tools():
    data = recipes.start_here()
    assert any("NO uses navegador" in rule for rule in data["reglas_de_oro"])
    assert set(data["recetas"]) == set(recipes.RECIPES)
    listed = {t["tool"] for tools in data["tools_por_area"].values() for t in tools}
    assert listed == set(recipes.TOOL_CATALOG)
    with pytest.raises(ValueError, match="Próximo paso"):
        recipes.get_recipe("no-existe")


def test_entry_tools_prompts_and_resources_over_stdio(tmp_path):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "meli_mcp", "serve"], env={"MELI_MCP_STATE_DIR": str(tmp_path), "PATH": ""})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert "NO uses navegador" in init.instructions
                start = await session.call_tool("meli_start_here", {})
                assert not start.isError and "combos" in start.structuredContent["recetas"]
                found = await session.call_tool("meli_find_tool", {"query": "armar combos con imagen"})
                assert found.structuredContent["recipes"][0]["recipe"] == "combos"
                recipe = await session.call_tool("meli_get_recipe", {"recipe_id": "combos"})
                assert [s["tool"] for s in recipe.structuredContent["steps"]][:2] == ["meli_my_items", "meli_combo_plan"]
                prompts = {p.name for p in (await session.list_prompts()).prompts}
                assert {"receta_combos", "receta_precios_masivos"} <= prompts
                prompt = await session.get_prompt("receta_combos")
                assert "meli_combo_plan" in prompt.messages[0].content.text
                resource = await session.read_resource("mercadolibre://recetas/combos")
                assert "meli_upload_picture" in resource.contents[0].text
                status = await session.call_tool("meli_status", {})
                assert status.structuredContent["ok"] is False and "doctor" in status.structuredContent["proximo_paso"]
                bad = await session.call_tool("meli_get_recipe", {"recipe_id": "zzz"})
                assert bad.isError
    asyncio.run(run())


# ------------------------------------------------------------------ meli_status / my_items / combo_plan

def test_status_reports_mode_scopes_and_account(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    write_private(tmp_path / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 99, "scope": "offline_access read"})
    monkeypatch.setattr(server, "api", Api({"/users/me": {"id": 5, "nickname": "N", "site_id": "MLA", "email": "secret@example.invalid"}}))
    status = server.meli_status()
    assert status["ok"] and status["cuenta"] == {"id": 5, "nickname": "N", "site_id": "MLA"} and "write" in " ".join(status["avisos"]) and "Lectura y escritura" in status["proximo_paso"]


def item(i, upid="MLAU%d", **extra):
    base = {"id": f"MLA{i}", "seller_id": 5, "title": f"Producto {i}", "price": 100 * i, "currency_id": "ARS", "status": "active", "condition": "new", "available_quantity": 10,
            "listing_type_id": "gold_special", "category_id": "MLA1", "user_product_id": (upid % i) if upid else None, "thumbnail": "http://t", "pictures": [{"secure_url": f"https://img/{i}.jpg"}]}
    return {"code": 200, "body": {**base, **extra}}


def test_my_items_returns_compact_rows_in_one_call(monkeypatch):
    api = Api({"/users/me": {"id": 5}, "/users/5/items/search": {"results": ["MLA1", "MLA2"], "paging": {"total": 2}}, "/items/bulk": [item(1), item(2)]})
    monkeypatch.setattr(server, "api", api)
    result = server.meli_my_items(limit=2, title_contains="producto 2")
    assert [i["item_id"] for i in result["items"]] == ["MLA2"] and result["items"][0]["user_product_id"] == "MLAU2" and result["items"][0]["pictures"] == ["https://img/2.jpg"]
    assert api.calls[1]["query"] == {"limit": 2, "offset": 0, "status": "active"}
    with pytest.raises(ValueError, match="Próximo paso"):
        server.meli_my_items(limit=99)


def test_combo_plan_builds_the_exact_kit_request_without_writing(monkeypatch):
    api = Api({"/users/me": {"id": 5}, "/items/bulk": [item(1), item(2)], "/user-products/": lambda q: {"bundles": []}})
    monkeypatch.setattr(server, "api", api)
    plan = server.meli_combo_plan(["MLA1", "MLA2"], quantities=[1, 2])
    assert plan["can_create_kit"] and plan["problems"] == []
    assert all(call["method"] == "GET" for call in api.calls)
    request = plan["kit_request"]["arguments"]
    assert request["method"] == "POST" and request["path"] == "/items/kits"
    body = request["body"]
    assert body["channels"] == ["marketplace"] and body["price"] == 500.0 and body["currency_id"] == "ARS" and body["listing_type_id"] == "gold_special"
    assert body["thumbnail"]["id"].startswith("<PICTURE_ID")
    assert [(c["user_product_id"], c["quantity"], c["automatic_price"]) for c in body["bundle"]["components"]] == [("MLAU1", 1, None), ("MLAU2", 2, None)]
    assert body["family_name"] == "Combo: Producto 1 + 2 x Producto 2"
    assert plan["image_inputs"][0]["photos"] == ["https://img/1.jpg"] and any("1200x1200" in r for r in plan["image_rules"]) and any("fondo blanco" in r for r in plan["image_rules"])
    synced = server.meli_combo_plan(["MLA1", "MLA2"], discount_pct=10)["kit_request"]["arguments"]["body"]
    assert "price" not in synced and synced["bundle"]["components"][0]["automatic_price"] == {"discount": 0.1}


def test_combo_plan_explains_what_blocks_a_kit(monkeypatch):
    api = Api({"/users/me": {"id": 5}, "/items/bulk": [item(1, status="paused"), item(2, upid=None), item(3, seller_id=9)], "/user-products/": {"bundles": []}})
    monkeypatch.setattr(server, "api", api)
    plan = server.meli_combo_plan(["MLA1", "MLA2", "MLA3"])
    text = " ".join(plan["problems"])
    assert not plan["can_create_kit"] and "paused" in text and "user_product_id" in text and "no pertenece" in text and "kit_request" not in plan
    with pytest.raises(ValueError, match="entre 2 y 6"):
        server.meli_combo_plan(["MLA1"])
    with pytest.raises(ValueError, match="quantities"):
        server.meli_combo_plan(["MLA1", "MLA2"], quantities=[1, 11])


# ------------------------------------------------------------------ pictures

def test_image_rules_and_signature_detection():
    assert images.inspect_image(png(600, 600)).as_dict() == {"format": "png", "width": 600, "height": 600, "bytes": len(png(600, 600))}
    assert images.inspect_image(jpeg(800, 600)).as_dict()["height"] == 600
    for bad in (b"GIF89a....", b"%PDF-1.4", b"{\"access_token\": \"secret\"}", b"", b"\xff\xd8\xff"):
        with pytest.raises(ValueError):
            images.inspect_image(bad)
    assert images.check_rules(images.inspect_image(png(400, 400)))["errors"]
    warnings = images.check_rules(images.inspect_image(jpeg(800, 600)))
    assert not warnings["errors"] and any("cuadrada" in w for w in warnings["warnings"])


def test_upload_picture_from_local_file_sends_multipart(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path / "estado"))
    write_private(tmp_path / "estado" / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 999})
    seen = {}
    def handler(request):
        seen.update(path=request.url.path, ctype=request.headers["content-type"], auth=request.headers["authorization"], body=request.content)
        return httpx.Response(201, json={"id": "123-MLA456_112021", "variations": [{"size": "500x500", "secure_url": "https://http2.mlstatic.com/D_NQ_NP_123-MLA456_112021-F.jpg"}]})
    monkeypatch.setattr(server, "api", MeliClient(httpx.MockTransport(handler)))
    path = tmp_path / "combo.png"
    path.write_bytes(png(600, 600))
    result = server.meli_upload_picture({"file_path": str(path)})
    assert result["picture_id"] == "123-MLA456_112021" and result["secure_url"].startswith("https://http2.mlstatic.com") and result["uploaded"]
    assert seen["path"] == "/pictures/items/upload" and seen["ctype"].startswith("multipart/form-data") and seen["auth"] == "Bearer t"
    assert b'name="file"' in seen["body"] and b'filename="combo.png"' in seen["body"] and b"\x89PNG" in seen["body"]
    assert any("1200x1200" in w for w in result["warnings"])


def test_upload_picture_accepts_base64_and_rejects_everything_that_is_not_a_real_image(tmp_path, monkeypatch):
    import base64
    api = Api({"/pictures/items/upload": {"id": "1-MLA2_012026", "variations": []}})
    monkeypatch.setattr(server, "api", api)
    result = server.meli_upload_picture({"base64": "data:image/png;base64," + base64.b64encode(png(520, 520)).decode()})
    assert result["picture_id"] == "1-MLA2_012026"
    name, data, ctype = api.calls[0]["files"]["file"]
    assert name.endswith(".png") and data.startswith(b"\x89PNG") and ctype == "image/png"
    secret = tmp_path / "token.json"
    secret.write_text('{"access_token": "SECRET"}')
    fake_png = tmp_path / "trampa.png"
    fake_png.write_text("no soy una imagen")
    for source in ({"file_path": str(secret)}, {"file_path": str(fake_png)}, {"file_path": str(tmp_path / "no-existe.png")}, {"file_path": str(tmp_path)}, {"base64": "AAAA"}, {"file_path": "a", "url": "https://x"}, {}):
        with pytest.raises(MeliError, match="Próximo paso|exactamente"):
            server.meli_upload_picture(source)
    small = tmp_path / "chica.png"
    small.write_bytes(png(300, 300))
    with pytest.raises(MeliError, match="500x500"):
        server.meli_upload_picture({"file_path": str(small)})
    assert len(api.calls) == 1


def test_upload_picture_from_url_is_https_public_only(monkeypatch):
    import socket
    api = Api({"/pictures/items/upload": {"id": "9-MLA9_012026"}})
    monkeypatch.setattr(server, "api", api)
    monkeypatch.setattr(images, "_transport", httpx.MockTransport(lambda request: httpx.Response(200, content=png(520, 520))))
    public = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(images.socket, "getaddrinfo", public)
    assert server.meli_upload_picture({"url": "https://cdn.example/foto.png"})["picture_id"] == "9-MLA9_012026"
    with pytest.raises(MeliError, match="HTTPS"):
        server.meli_upload_picture({"url": "http://cdn.example/foto.png"})
    monkeypatch.setattr(images.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(MeliError, match="no pública"):
        server.meli_upload_picture({"url": "https://localhost/foto.png"})
    monkeypatch.setattr(images.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))])
    with pytest.raises(MeliError, match="no pública"):
        server.meli_upload_picture({"url": "https://metadata.example/foto.png"})
    assert len(api.calls) == 1


def test_read_only_mode_blocks_picture_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MELI_MCP_MODE", "read-only")
    write_private(tmp_path / "estado" / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 999})
    monkeypatch.setattr(server, "api", MeliClient(httpx.MockTransport(lambda request: httpx.Response(201, json={"id": "x"}))))
    path = tmp_path / "a.png"
    path.write_bytes(png(520, 520))
    with pytest.raises(MeliError, match="SOLO LECTURA"):
        server.meli_upload_picture({"file_path": str(path)})


def test_docs_mention_the_real_tool_count_and_the_entry_points():
    from pathlib import Path
    root = Path(__file__).parent.parent
    n = len(registered())
    assert f"{n} herramientas" in (root / "README.md").read_text(encoding="utf-8") and f"{n} tools" in (root / "docs" / "README-en.md").read_text(encoding="utf-8")
    for name in ("README.md", "AGENTS.md", "llms.txt"):
        assert "docs/AGENT_INSTALL.md" in (root / name).read_text(encoding="utf-8")[:1500], name
    assert (root / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert "0.2.0" in (root / "CHANGELOG.md").read_text(encoding="utf-8")

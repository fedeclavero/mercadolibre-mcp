from urllib.parse import parse_qs
import httpx
import pytest
from meli_mcp import cli
from meli_mcp.client import MeliClient, MeliError, load_private, write_private

@pytest.fixture
def oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path))
    config = {"client_id": "123", "client_secret": "secret-test", "redirect_uri": "https://owner.example/callback", "country": "ar", "pkce": True}
    write_private(tmp_path / "config.json", config)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "state-test" if n == 32 else "verifier-test")
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    return tmp_path, config

def test_authorize_exchanges_code_with_pkce(oauth, monkeypatch):
    tmp, config = oauth
    captured = []
    def handler(req):
        captured.append(parse_qs(req.content.decode()))
        return httpx.Response(200, json={"access_token": "dummy-access", "refresh_token": "dummy-refresh", "expires_in": 3600})
    monkeypatch.setattr(MeliClient, "_client", lambda self: httpx.Client(base_url="https://api.mercadolibre.com", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "https://owner.example/callback?code=dummy-code&state=state-test")
    cli.authorize()
    assert captured[0]["code_verifier"] == ["verifier-test"]
    assert load_private("token.json")["refresh_token"] == "dummy-refresh"

@pytest.mark.parametrize("query", ["code=x&state=wrong", "code=x&state=state-test&state=state-test", "state=state-test"])
def test_authorize_rejects_invalid_callback(oauth, monkeypatch, query):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "https://owner.example/callback?" + query)
    with pytest.raises(MeliError): cli.authorize()
    assert not (oauth[0] / "token.json").exists()

def test_authorize_rejects_config_changed_mid_flow(oauth, monkeypatch):
    tmp, config = oauth
    def changed(_):
        write_private(tmp / "config.json", {**config, "client_id": "other"})
        return "https://owner.example/callback?code=x&state=state-test"
    monkeypatch.setattr(cli.getpass, "getpass", changed)
    with pytest.raises(MeliError, match="configuración cambió"): cli.authorize()
    assert not (tmp / "token.json").exists()

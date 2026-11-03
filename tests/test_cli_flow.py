import io
import json
import os
import socket
import threading
import time
import tomllib
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from meli_mcp import __version__, authflow, cli, doctor
from meli_mcp.client import MeliClient, MeliError, access_mode, load_private, write_private


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path / "estado"))
    home = tmp_path / "home"          # keep doctor/install away from the real user's client configs
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setattr("meli_mcp.installer.shutil.which", lambda name: None)
    for name in ("MELI_CLIENT_ID", "MELI_CLIENT_SECRET", "MELI_REDIRECT_URI", "MELI_COUNTRY", "MELI_PKCE", "MELI_MCP_MODE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "estado"


def run_cli(monkeypatch, capsys, *argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    capsys.readouterr()
    try:
        cli.main(list(argv))
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out = capsys.readouterr()
    return code, out.out, out.err


def token_endpoint(monkeypatch, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "dummy-access", "refresh_token": "dummy-refresh", "expires_in": 3600, "scope": "offline_access read write", "user_id": 1})
    monkeypatch.setattr(MeliClient, "_client", lambda self: httpx.Client(base_url="https://api.mercadolibre.com", transport=httpx.MockTransport(handler)))


def test_version_flag_matches_package_and_pyproject(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"mercadolibre-mcp {__version__}"
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_setup_is_non_interactive_with_flags_and_stdin_secret(state, monkeypatch, capsys):
    code, out, err = run_cli(monkeypatch, capsys, "setup", "--client-id", "123", "--client-secret-stdin", "--country", "ar", "--json", stdin="super-secret-value\n")
    assert code == 0
    result = json.loads(out)
    assert result["ok"] and result["pkce"] is True and result["mode"] == "full" and result["redirect_uri"] == authflow.DEFAULT_REDIRECT
    assert "super-secret-value" not in out + err
    config = load_private("config.json")
    assert config == {"client_id": "123", "client_secret": "super-secret-value", "redirect_uri": authflow.DEFAULT_REDIRECT, "country": "ar", "pkce": True}
    if os.name != "nt":
        assert (state / "config.json").stat().st_mode & 0o777 == 0o600


def test_setup_reads_environment_variables(state, monkeypatch, capsys):
    monkeypatch.setenv("MELI_CLIENT_ID", "999"); monkeypatch.setenv("MELI_CLIENT_SECRET", "s3"); monkeypatch.setenv("MELI_REDIRECT_URI", "https://mi.dominio/cb")
    monkeypatch.setenv("MELI_COUNTRY", "mx"); monkeypatch.setenv("MELI_PKCE", "false")
    code, out, _ = run_cli(monkeypatch, capsys, "setup")
    assert code == 0
    assert load_private("config.json") == {"client_id": "999", "client_secret": "s3", "redirect_uri": "https://mi.dominio/cb", "country": "mx", "pkce": False}


def test_setup_without_data_fails_with_next_step_and_never_prompts(state, monkeypatch, capsys):
    code, out, err = run_cli(monkeypatch, capsys, "setup")
    assert code == 1 and "Próximo paso" in err and "--client-secret-stdin" in err
    assert not (state / "config.json").exists()


@pytest.mark.parametrize("uri", ["http://localhost:8765/callback", "https://u:p@x.com/cb", "https://x.com/cb#frag", "nada"])
def test_setup_rejects_non_https_redirects(state, monkeypatch, capsys, uri):
    code, _, err = run_cli(monkeypatch, capsys, "setup", "--client-id", "1", "--client-secret", "s", "--redirect-uri", uri)
    assert code == 1 and "HTTPS" in err


def test_rerunning_setup_keeps_authorization_for_same_app_and_drops_it_for_another(state, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "setup", "--client-id", "1", "--client-secret", "a", "--mode", "read-only")
    write_private(state / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 999})
    run_cli(monkeypatch, capsys, "setup", "--client-id", "1", "--client-secret", "b", "--country", "cl")
    assert (state / "token.json").exists() and load_private("config.json")["mode"] == "read_only"
    run_cli(monkeypatch, capsys, "setup", "--client-id", "2", "--client-secret", "c")
    assert not (state / "token.json").exists()


def setup_app(state, redirect="https://owner.example/callback", pkce=True):
    write_private(state / "config.json", {"client_id": "123", "client_secret": "secret-test", "redirect_uri": redirect, "country": "ar", "pkce": pkce})


def test_authorize_start_then_finish_with_callback_url(state, monkeypatch, capsys):
    setup_app(state)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(authflow.secrets, "token_urlsafe", lambda n: "state-test" if n == 32 else "verifier-test")
    code, out, _ = run_cli(monkeypatch, capsys, "authorize", "--start", "--json")
    started = json.loads(out)
    assert code == 0 and started["authorize_url"].startswith("https://auth.mercadolibre.com.ar/authorization?") and "code_challenge_method=S256" in started["authorize_url"]
    if os.name != "nt":
        assert (state / "pending-auth.json").stat().st_mode & 0o777 == 0o600
    seen = []
    token_endpoint(monkeypatch, seen)
    code, out, err = run_cli(monkeypatch, capsys, "authorize", "--finish", "--callback-url", "https://owner.example/callback?code=dummy-code&state=state-test", "--json")
    assert code == 0 and json.loads(out)["authorized"] is True
    assert seen[0]["code"] == ["dummy-code"] and seen[0]["code_verifier"] == ["verifier-test"] and seen[0]["redirect_uri"] == ["https://owner.example/callback"]
    assert load_private("token.json")["refresh_token"] == "dummy-refresh" and not (state / "pending-auth.json").exists()
    assert "dummy-code" not in out + err


def test_authorize_finish_accepts_a_bare_code_and_rejects_bad_state(state, monkeypatch, capsys):
    setup_app(state, pkce=False)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    run_cli(monkeypatch, capsys, "authorize", "--start", "--no-open", "--json")
    token_endpoint(monkeypatch)
    code, _, err = run_cli(monkeypatch, capsys, "authorize", "--finish", "--callback-url", "https://owner.example/callback?code=x&state=otro")
    assert code == 1 and "Próximo paso" in err and not (state / "token.json").exists()
    code, _, _ = run_cli(monkeypatch, capsys, "authorize", "--finish", "--code", "TG-bare-code")
    assert code == 0 and (state / "token.json").exists()


def test_authorize_finish_without_start_or_expired(state, monkeypatch, capsys):
    setup_app(state)
    code, _, err = run_cli(monkeypatch, capsys, "authorize", "--finish", "--code", "x")
    assert code == 1 and "--start" in err
    write_private(state / "pending-auth.json", {"state": "s", "verifier": "v", "client_id": "123", "redirect_uri": "https://owner.example/callback", "created_at": time.time() - 5000})
    code, _, err = run_cli(monkeypatch, capsys, "authorize", "--finish", "--code", "x")
    assert code == 1 and "venció" in err


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_authorize_listens_on_https_localhost_with_self_signed_certificate(state, monkeypatch, capsys):
    port = free_port()
    redirect = f"https://localhost:{port}/callback"
    setup_app(state, redirect=redirect)
    monkeypatch.setattr(authflow.secrets, "token_urlsafe", lambda n: "state-test" if n == 32 else "verifier-test")
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    seen = []
    token_endpoint(monkeypatch, seen)
    results = {}
    ready = threading.Event()
    thread = threading.Thread(target=lambda: results.update(cli.authorize(timeout=20, as_json=True, ready=ready)))
    thread.start()
    assert ready.wait(timeout=10), "Server did not start listening in time"
    deadline = time.time() + 15
    reply = None
    while time.time() < deadline:
        try:
            wrong = httpx.get(f"https://127.0.0.1:{port}/otra-ruta", verify=False, timeout=5)
            assert wrong.status_code == 404
            reply = httpx.get(f"https://127.0.0.1:{port}/callback?code=dummy-code&state=state-test", headers={"Host": f"localhost:{port}"}, verify=False, timeout=5)
            break
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(0.1)
    thread.join(timeout=20)
    assert reply.status_code == 200 and "dummy-code" not in reply.text
    assert results.get("authorized") is True and seen[0]["code"] == ["dummy-code"]
    assert opened and opened[0].startswith("https://auth.mercadolibre.com.ar/authorization?")
    assert load_private("token.json")["access_token"] == "dummy-access"
    out = capsys.readouterr()
    assert "dummy-code" not in out.out + out.err


def test_listener_times_out_with_next_step(state):
    port = free_port()
    with pytest.raises(MeliError, match="Próximo paso"):
        authflow.listen_for_callback(f"https://localhost:{port}/callback", timeout=0.6)


def test_listener_refuses_non_local_redirects():
    with pytest.raises(MeliError, match="--start"):
        authflow.listen_for_callback("https://owner.example/callback", timeout=1)


def test_legacy_interactive_authorize_still_works_when_redirect_is_not_local(state, monkeypatch):
    setup_app(state)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "state-test" if n == 32 else "verifier-test")
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "https://owner.example/callback?code=c&state=state-test")
    token_endpoint(monkeypatch)
    assert cli.authorize()["authorized"] is True


def test_mode_command_and_read_only_enforcement(state, monkeypatch, capsys):
    setup_app(state)
    write_private(state / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600})
    assert access_mode() == "full"
    code, out, _ = run_cli(monkeypatch, capsys, "mode", "read-only", "--json")
    assert code == 0 and json.loads(out)["mode"] == "read_only" and access_mode() == "read_only"
    assert load_private("config.json")["client_secret"] == "secret-test"
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"ok": True})
    client = MeliClient(httpx.MockTransport(handler))
    assert client.request("GET", "/users/me") == {"ok": True}
    with pytest.raises(MeliError, match="SOLO LECTURA.*Próximo paso"):
        client.request("PUT", "/items/MLA1", body={"price": 1})
    assert calls == ["GET"]
    monkeypatch.setenv("MELI_MCP_MODE", "full")
    assert client.request("PUT", "/items/MLA1", body={"price": 1}) == {"ok": True}
    monkeypatch.delenv("MELI_MCP_MODE")
    run_cli(monkeypatch, capsys, "mode", "full")
    assert access_mode() == "full"


def test_403_names_the_missing_permission_group_and_how_to_fix_it(state):
    write_private(state / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600})
    client = MeliClient(httpx.MockTransport(lambda request: httpx.Response(403, json={"error": "forbidden"})))
    with pytest.raises(MeliError) as error:
        client.request("POST", "/items", body={})
    text = str(error.value)
    assert "Publicación y sincronización" in text and "Lectura y escritura" in text and "authorize" in text and "Próximo paso" in text
    with pytest.raises(MeliError) as error:
        client.request("GET", "/questions/search")
    assert "Comunicación pre y postventa" in str(error.value) and "Solo lectura" in str(error.value)


def test_doctor_without_setup_gives_next_step(state):
    report = doctor.run(offline=True)
    assert not report["ok"] and "setup" in report["next_step"]
    assert {c["check"] for c in report["checks"]} >= {"version", "configuracion", "autorizacion", "modo", "clientes_mcp"}


def test_doctor_ok_offline_after_setup_and_authorization(state, monkeypatch, capsys):
    setup_app(state)
    write_private(state / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600, "scope": "offline_access read write"})
    code, out, _ = run_cli(monkeypatch, capsys, "doctor", "--offline", "--json")
    report = json.loads(out)
    assert report["ok"] and code == 0
    assert next(c for c in report["checks"] if c["check"] == "permisos")["ok"]
    write_private(state / "token.json", {"access_token": "t", "refresh_token": "r", "expires_at": 0, "scope": "read"})
    report = doctor.run(offline=True)
    assert not report["ok"] and "Lectura y escritura" in report["next_step"]


def test_devcenter_guide_lists_every_permission_and_the_redirect(monkeypatch, capsys):
    code, out, _ = run_cli(monkeypatch, capsys, "devcenter", "--country", "mx", "--json")
    guide = json.loads(out)
    text = json.dumps(guide, ensure_ascii=False)
    assert guide["devcenter_url"] == "https://developers.mercadolibre.com.mx/devcenter/" and authflow.DEFAULT_REDIRECT in text
    for group in ("Publicación y sincronización", "Comunicación pre y postventa", "Ventas y envíos", "Promociones, cupones y descuentos", "Facturación", "Publicidad", "Métricas del negocio"):
        assert group in text
    assert "Lectura y escritura" in text


def test_install_command_json_output_and_exit_codes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("USERPROFILE", str(home)); monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr("meli_mcp.installer.shutil.which", lambda name: "/usr/bin/uvx" if name == "uvx" else None)
    monkeypatch.setattr(cli.installer, "verify_launch", lambda spec, timeout=240: {"ok": True, "server": "mercadolibre_mcp", "version": __version__, "tools": 48, "has_start_here": True})
    code, out, _ = run_cli(monkeypatch, capsys, "install", "--client", "all", "--json")
    result = json.loads(out)
    assert code == 0 and result["ok"] and [i["client"] for i in result["installed"]] == ["cursor"] and result["installed"][0]["verified"]
    assert result["launch_check"]["ok"] and any("reinici" in step.lower() or "cierre" in step.lower() for step in result["next_steps"])
    code, out, _ = run_cli(monkeypatch, capsys, "install", "--client", "cursor", "--json")
    assert json.loads(out)["installed"][0]["status"] == "unchanged"
    code, out, _ = run_cli(monkeypatch, capsys, "install", "--client", "all", "--dry-run", "--json")
    assert json.loads(out)["dry_run"] is True
    empty = tmp_path / "empty"; empty.mkdir()
    monkeypatch.setenv("HOME", str(empty)); monkeypatch.setenv("USERPROFILE", str(empty)); monkeypatch.setenv("XDG_CONFIG_HOME", str(empty / ".config"))
    code, out, _ = run_cli(monkeypatch, capsys, "install", "--client", "all", "--json")
    assert code == 1 and json.loads(out)["not_detected"] and "No se detectó" in json.loads(out)["next_steps"][0]

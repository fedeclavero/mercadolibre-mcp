import json
import tomllib
from pathlib import Path

import pytest

from meli_mcp import installer
from meli_mcp.installer import CLIENTS, Env, InstallError, ServerSpec, default_spec, install_client, loads_lenient

SPEC = ServerSpec("mercadolibre", "/usr/bin/uvx", ["--from", "git+https://github.com/fedeclavero/mercadolibre-mcp", "mercadolibre-mcp", "serve"])


def make_env(tmp_path: Path, system: str = "linux", **extra) -> Env:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    appdata = home / "AppData" / "Roaming" if system == "windows" else None
    return Env(system, home, appdata, home / ".config", tmp_path / "cwd", {}, lambda name: None, **extra)


def read_json(path: Path):
    return loads_lenient(path.read_text(encoding="utf-8"))[0]


@pytest.mark.parametrize("system,expected", [
    ("linux", ".config/Claude/claude_desktop_config.json"),
    ("darwin", "Library/Application Support/Claude/claude_desktop_config.json"),
    ("windows", "AppData/Roaming/Claude/claude_desktop_config.json"),
])
def test_claude_desktop_path_per_os(tmp_path, system, expected):
    env = make_env(tmp_path, system)
    assert installer.target_files(CLIENTS["claude-desktop"], env, "user", None) == [env.home / expected]


@pytest.mark.parametrize("system,expected", [
    ("linux", ".config/Code/User/mcp.json"),
    ("darwin", "Library/Application Support/Code/User/mcp.json"),
    ("windows", "AppData/Roaming/Code/User/mcp.json"),
])
def test_vscode_path_per_os(tmp_path, system, expected):
    env = make_env(tmp_path, system)
    assert installer.target_files(CLIENTS["vscode"], env, "user", None) == [env.home / expected]


def test_windsurf_uses_cascade_file_and_legacy_only_if_present(tmp_path):
    env = make_env(tmp_path)
    assert installer.target_files(CLIENTS["windsurf"], env, "user", None) == [env.home / ".config/devin/mcp_config.json"]
    legacy = env.home / ".codeium/windsurf/mcp_config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    assert installer.target_files(CLIENTS["windsurf"], env, "user", None) == [env.home / ".config/devin/mcp_config.json", legacy]
    win = make_env(tmp_path / "w", "windows")
    assert installer.target_files(CLIENTS["windsurf"], win, "user", None) == [win.home / "AppData/Roaming/devin/mcp_config.json"]


def test_project_scope_paths(tmp_path):
    env = make_env(tmp_path)
    project = tmp_path / "proj"
    expected = {"claude-code": ".mcp.json", "cursor": ".cursor/mcp.json", "vscode": ".vscode/mcp.json", "gemini-cli": ".gemini/settings.json",
                "codex": ".codex/config.toml", "antigravity": ".agents/mcp_config.json", "opencode": "opencode.json", "windsurf": ".devin/mcp_config.json"}
    for cid, rel in expected.items():
        assert installer.target_files(CLIENTS[cid], env, "project", project) == [project.resolve() / rel]
    with pytest.raises(InstallError, match="Próximo paso"):
        installer.target_files(CLIENTS["claude-desktop"], env, "project", project)


@pytest.mark.parametrize("cid", [c for c, v in CLIENTS.items() if not v.toml])
def test_json_clients_create_merge_idempotent_and_backup(tmp_path, cid):
    env = make_env(tmp_path)
    client = CLIENTS[cid]
    path = installer.target_files(client, env, "user", None)[0]
    first = install_client(cid, SPEC, env)[0]
    assert first.status == "created" and first.verified and first.backup is None
    again = install_client(cid, SPEC, env)[0]
    assert again.status == "unchanged"
    # another server and unrelated keys survive; the file is backed up before modification
    data = read_json(path)
    data["otra_clave"] = {"x": 1}
    data[client.container]["otro"] = {"command": "otro"}
    path.write_text(json.dumps(data))
    changed = ServerSpec("mercadolibre", "/opt/uvx", SPEC.args)
    outcome = install_client(cid, changed, env)[0]
    assert outcome.status == "updated" and outcome.backup and Path(outcome.backup).exists()
    final = read_json(path)
    assert final["otra_clave"] == {"x": 1} and final[client.container]["otro"] == {"command": "otro"}
    entry = final[client.container]["mercadolibre"]
    assert "/opt/uvx" in json.dumps(entry)
    assert json.loads(Path(outcome.backup).read_text(encoding="utf-8"))[client.container]["mercadolibre"] != entry


def test_entry_shapes_follow_each_client_format(tmp_path):
    env = make_env(tmp_path)
    for cid in CLIENTS:
        if not CLIENTS[cid].toml:
            install_client(cid, SPEC, env)
    get = lambda cid: read_json(installer.target_files(CLIENTS[cid], env, "user", None)[0])
    assert get("claude-code")["mcpServers"]["mercadolibre"] == {"type": "stdio", "command": "/usr/bin/uvx", "args": SPEC.args}
    assert get("vscode")["servers"]["mercadolibre"]["type"] == "stdio"
    assert get("opencode")["mcp"]["mercadolibre"] == {"type": "local", "command": ["/usr/bin/uvx", *SPEC.args], "enabled": True}
    assert get("zed")["context_servers"]["mercadolibre"] == {"command": "/usr/bin/uvx", "args": SPEC.args, "env": {}}
    assert get("gemini-cli")["mcpServers"]["mercadolibre"] == {"command": "/usr/bin/uvx", "args": SPEC.args}
    assert get("antigravity")["mcpServers"]["mercadolibre"]["command"] == "/usr/bin/uvx"


def test_state_dir_env_is_forwarded_to_the_client(tmp_path):
    env = make_env(tmp_path)
    env.environ["MELI_MCP_STATE_DIR"] = "/datos/meli"
    env.which = lambda name: "/usr/bin/uvx"
    spec = default_spec(env)
    assert spec.env == {"MELI_MCP_STATE_DIR": "/datos/meli"} and spec.command == "/usr/bin/uvx"
    install_client("cursor", spec, env)
    assert read_json(env.home / ".cursor/mcp.json")["mcpServers"]["mercadolibre"]["env"] == {"MELI_MCP_STATE_DIR": "/datos/meli"}


def test_default_spec_needs_uvx_and_local_mode_uses_this_python(tmp_path):
    env = make_env(tmp_path)
    with pytest.raises(InstallError, match="Próximo paso"):
        default_spec(env)
    local = default_spec(env, local=True)
    assert local.args == ["-m", "meli_mcp", "serve"]
    own = default_spec(env, command="/ruta/mercadolibre-mcp")
    assert own.argv == ["/ruta/mercadolibre-mcp", "serve"]


def test_foreign_server_with_same_name_is_not_overwritten_without_force(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".cursor/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"mercadolibre": {"command": "otro-programa", "args": []}}}))
    outcome = install_client("cursor", SPEC, env)[0]
    assert outcome.status == "conflict" and "--force" in outcome.detail
    assert read_json(path)["mcpServers"]["mercadolibre"]["command"] == "otro-programa"
    forced = install_client("cursor", SPEC, env, force=True)[0]
    assert forced.status == "updated" and forced.backup


def test_old_registration_of_this_server_is_recognised_and_updated(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".cursor/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"mercadolibre": {"command": "/home/x/mercadolibre-mcp/.venv/bin/mercadolibre-mcp", "args": ["serve"], "autoApprove": ["meli_account"]}}}))
    outcome = install_client("cursor", SPEC, env)[0]
    assert outcome.status == "updated"
    entry = read_json(path)["mcpServers"]["mercadolibre"]
    assert entry["command"] == "/usr/bin/uvx" and entry["autoApprove"] == ["meli_account"]


def test_jsonc_comments_are_tolerated_and_reported(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".config/zed/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('// mi config\n{\n  "theme": "One Dark", /* tema */\n  "context_servers": {},\n}\n')
    outcome = install_client("zed", SPEC, env)[0]
    assert outcome.status == "added" and outcome.warning and "comentarios" in outcome.warning and outcome.verified
    assert read_json(path)["theme"] == "One Dark"
    assert "// mi config" in Path(outcome.backup).read_text(encoding="utf-8")


def test_invalid_json_is_never_overwritten(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".cursor/mcp.json"
    path.parent.mkdir(parents=True)
    path.write_text("{no es json")
    outcome = install_client("cursor", SPEC, env)[0]
    assert outcome.status == "error" and "Próximo paso" in outcome.detail
    assert path.read_text(encoding="utf-8") == "{no es json"


def test_dry_run_writes_nothing(tmp_path):
    env = make_env(tmp_path)
    outcome = install_client("cursor", SPEC, env, dry_run=True)[0]
    assert outcome.status == "created" and not (env.home / ".cursor/mcp.json").exists()


def test_codex_toml_merge_preserves_other_settings_and_is_idempotent(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    original = 'model = "gpt-5"\n# comentario propio\n\n[mcp_servers.otro]\ncommand = "otro"\n\n[mcp_servers.mercadolibre]\ncommand = "/viejo/.venv/bin/mercadolibre-mcp"\nargs = ["serve"]\nenabled = false\n\n[mcp_servers.mercadolibre.env]\nX = "1"\n\n[profiles.p]\nmodel = "x"\n'
    path.write_text(original)
    outcome = install_client("codex", SPEC, env)[0]
    assert outcome.status == "updated" and outcome.verified and outcome.backup
    text = path.read_text(encoding="utf-8")
    assert "# comentario propio" in text and "[profiles.p]" in text
    data = tomllib.loads(text)
    assert data["model"] == "gpt-5" and data["mcp_servers"]["otro"] == {"command": "otro"}
    entry = data["mcp_servers"]["mercadolibre"]
    assert entry["command"] == "/usr/bin/uvx" and entry["args"] == SPEC.args and entry["startup_timeout_sec"] == 120 and entry["enabled"] is False
    assert install_client("codex", SPEC, env)[0].status == "unchanged"


def test_codex_creates_config_and_handles_windows_paths(tmp_path):
    env = make_env(tmp_path, "windows")
    spec = ServerSpec("mercadolibre", r"C:\Users\Ana\.local\bin\uvx.exe", SPEC.args)
    outcome = install_client("codex", spec, env)[0]
    assert outcome.status == "created"
    assert tomllib.loads((env.home / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["mercadolibre"]["command"] == r"C:\Users\Ana\.local\bin\uvx.exe"


def test_invalid_toml_is_not_touched(tmp_path):
    env = make_env(tmp_path)
    path = env.home / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("esto = = no es toml")
    assert install_client("codex", SPEC, env)[0].status == "error"
    assert path.read_text(encoding="utf-8") == "esto = = no es toml"


def test_detection_and_selection(tmp_path):
    env = make_env(tmp_path)
    assert installer.detected(env) == []
    (env.home / ".cursor").mkdir()
    (env.home / ".codex").mkdir()
    assert installer.detected(env) == ["codex", "cursor"]
    ids, skipped = installer.resolve_ids(["all"], env)
    assert ids == ["codex", "cursor"] and "zed" in skipped
    assert installer.resolve_ids(["zed,cline"], env)[0] == ["zed", "cline"]
    with pytest.raises(InstallError, match="Próximo paso"):
        installer.resolve_ids(["nope"], env)


def test_cline_updates_cli_file_and_existing_extension_file(tmp_path):
    env = make_env(tmp_path)
    ext = env.home / ".config/Code/User/globalStorage/saoudrizwan.claude-dev/settings"
    ext.mkdir(parents=True)
    files = installer.target_files(CLIENTS["cline"], env, "user", None)
    assert files[0] == env.home / ".cline/data/settings/cline_mcp_settings.json"
    assert ext / "cline_mcp_settings.json" not in files        # only updated when it already exists
    (ext / "cline_mcp_settings.json").write_text("{}")
    assert (ext / "cline_mcp_settings.json") in installer.target_files(CLIENTS["cline"], env, "user", None)


def test_installed_status_reports_registration(tmp_path):
    env = make_env(tmp_path)
    (env.home / ".cursor").mkdir()
    (env.home / ".codex").mkdir()
    install_client("cursor", SPEC, env)
    status = installer.installed_status("mercadolibre", env)
    assert status["cursor"]["registered"] is True and status["codex"]["registered"] is False


def test_launch_verification_runs_the_real_server_over_stdio(tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv("MELI_MCP_STATE_DIR", str(tmp_path / "estado"))
    result = installer.verify_launch(ServerSpec("mercadolibre", sys.executable, ["-m", "meli_mcp", "serve"], {"MELI_MCP_STATE_DIR": str(tmp_path / "estado")}), timeout=60)
    assert result["ok"] and result["tools"] >= 48 and result["has_start_here"]
    broken = installer.verify_launch(ServerSpec("mercadolibre", sys.executable, ["-c", "raise SystemExit(3)"]), timeout=20)
    assert not broken["ok"] and "Próximo paso" in broken["error"]

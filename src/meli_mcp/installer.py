"""Registers this server in the user's MCP clients without hand-editing JSON/TOML.

Per client: locate its config (official docs, see docs/AGENT_INSTALL.md), merge only our entry, keep every other
entry and key, back up the file before changing it, write atomically, re-read to verify, and be idempotent.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_NAME = "mercadolibre"
DEFAULT_SOURCE = "git+https://github.com/fedeclavero/mercadolibre-mcp"
OURS = ("mercadolibre-mcp", "meli_mcp", "mercadolibre_mcp")


class InstallError(Exception):
    """An actionable error; the message ends with the next step."""


# --------------------------------------------------------------------------------------------- environment

@dataclass
class Env:
    system: str
    home: Path
    appdata: Path | None = None
    xdg_config: Path | None = None
    cwd: Path = field(default_factory=Path.cwd)
    environ: dict[str, str] = field(default_factory=dict)
    which: Callable[[str], str | None] = field(default=lambda name: shutil.which(name))

    @classmethod
    def current(cls) -> "Env":
        system = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
        home = Path.home()
        appdata = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else (home / "AppData" / "Roaming" if system == "windows" else None)
        xdg = Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else home / ".config"
        return cls(system, home, appdata, xdg, Path.cwd(), dict(os.environ))

    @property
    def config_home(self) -> Path:
        return self.xdg_config or self.home / ".config"

    def exists(self, path: Path) -> bool:
        return path.exists()


# --------------------------------------------------------------------------------------------- server entry

@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def argv(self) -> list[str]:
        return [self.command, *self.args]


def default_spec(env: Env, *, name: str = DEFAULT_NAME, source: str = DEFAULT_SOURCE, command: str | None = None, local: bool = False) -> ServerSpec:
    """`uvx --from <source> mercadolibre-mcp serve` (absolute uvx path when found, so GUI apps do not depend on PATH)."""
    extra = {"MELI_MCP_STATE_DIR": env.environ["MELI_MCP_STATE_DIR"]} if env.environ.get("MELI_MCP_STATE_DIR") else {}
    if local:
        return ServerSpec(name, command or sys.executable, ["-m", "meli_mcp", "serve"], extra)
    if command:
        return ServerSpec(name, command, ["serve"], extra)
    runner = env.which("uvx")
    if not runner:
        raise InstallError("No se encontró `uvx` (viene con uv). Próximo paso: instalá uv (ver docs/AGENT_INSTALL.md, paso 1) y volvé a ejecutar el comando.")
    return ServerSpec(name, runner, ["--from", source, "mercadolibre-mcp", "serve"], extra)


def _json_entry(spec: ServerSpec, *, typed: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": spec.command, "args": list(spec.args)}
    if typed:
        entry = {"type": "stdio", **entry}
    if spec.env:
        entry["env"] = dict(spec.env)
    return entry


def _opencode_entry(spec: ServerSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "local", "command": spec.argv, "enabled": True}
    if spec.env:
        entry["environment"] = dict(spec.env)
    return entry


# --------------------------------------------------------------------------------------------- client registry

@dataclass(frozen=True)
class Client:
    id: str
    label: str
    container: str                      # JSON key that holds the servers ("" for TOML)
    build: Callable[[ServerSpec], dict[str, Any]] | None
    user_paths: Callable[[Env], list[Path]]
    project_path: Callable[[Path], Path] | None
    detect: Callable[[Env], bool]
    toml: bool = False
    optional_paths: Callable[[Env], list[Path]] | None = None   # extra files updated only if they already exist
    note: str = ""


def _which_any(env: Env, *names: str) -> bool:
    return any(env.which(n) for n in names)


def _vscode_user_dirs(env: Env) -> list[Path]:
    if env.system == "darwin":
        base = env.home / "Library" / "Application Support"
    elif env.system == "windows":
        base = env.appdata or env.home / "AppData" / "Roaming"
    else:
        base = env.config_home
    return [base / "Code" / "User", base / "Code - Insiders" / "User"]


def _claude_desktop(env: Env) -> Path:
    if env.system == "darwin":
        return env.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if env.system == "windows":
        return (env.appdata or env.home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
    return env.home / ".config" / "Claude" / "claude_desktop_config.json"


def _codex_home(env: Env) -> Path:
    return Path(env.environ["CODEX_HOME"]) if env.environ.get("CODEX_HOME") else env.home / ".codex"


def _windsurf_main(env: Env) -> Path:
    if env.system == "windows":
        return (env.appdata or env.home / "AppData" / "Roaming") / "devin" / "mcp_config.json"
    return env.config_home / "devin" / "mcp_config.json"


def _opencode_path(env: Env) -> Path:
    base = env.config_home / "opencode" if env.system != "windows" else env.home / ".config" / "opencode"
    jsonc = base / "opencode.jsonc"
    return jsonc if jsonc.exists() and not (base / "opencode.json").exists() else base / "opencode.json"


def _cline_paths(env: Env) -> list[Path]:
    if env.environ.get("CLINE_MCP_SETTINGS_PATH"):
        return [Path(env.environ["CLINE_MCP_SETTINGS_PATH"])]
    return [env.home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"]


def _cline_extension_paths(env: Env) -> list[Path]:
    return [d / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json" for d in _vscode_user_dirs(env)]


def _zed_path(env: Env) -> Path:
    if env.system == "windows":
        return (env.appdata or env.home / "AppData" / "Roaming") / "Zed" / "settings.json"
    return env.home / ".config" / "zed" / "settings.json"


def _build_clients() -> dict[str, Client]:
    clients = [
        Client("claude-code", "Claude Code", "mcpServers", lambda s: _json_entry(s, typed=True),
               lambda e: [e.home / ".claude.json"], lambda d: d / ".mcp.json",
               lambda e: _which_any(e, "claude") or (e.home / ".claude.json").exists() or (e.home / ".claude").is_dir()),
        Client("claude-desktop", "Claude Desktop", "mcpServers", lambda s: _json_entry(s, typed=False),
               lambda e: [_claude_desktop(e)], None, lambda e: _claude_desktop(e).parent.is_dir()),
        Client("codex", "Codex (CLI, app e IDE)", "", None,
               lambda e: [_codex_home(e) / "config.toml"], lambda d: d / ".codex" / "config.toml",
               lambda e: _which_any(e, "codex") or _codex_home(e).is_dir(), toml=True),
        Client("gemini-cli", "Gemini CLI", "mcpServers", lambda s: _json_entry(s, typed=False),
               lambda e: [e.home / ".gemini" / "settings.json"], lambda d: d / ".gemini" / "settings.json",
               lambda e: _which_any(e, "gemini") or (e.home / ".gemini" / "settings.json").exists()),
        Client("cursor", "Cursor", "mcpServers", lambda s: _json_entry(s, typed=True),
               lambda e: [e.home / ".cursor" / "mcp.json"], lambda d: d / ".cursor" / "mcp.json",
               lambda e: _which_any(e, "cursor") or (e.home / ".cursor").is_dir()),
        Client("windsurf", "Windsurf", "mcpServers", lambda s: _json_entry(s, typed=False),
               lambda e: [_windsurf_main(e)], lambda d: d / ".devin" / "mcp_config.json",
               lambda e: _which_any(e, "windsurf") or (e.home / ".codeium" / "windsurf").is_dir() or _windsurf_main(e).parent.is_dir(),
               optional_paths=lambda e: [e.home / ".codeium" / "windsurf" / "mcp_config.json"],
               note="Se escribe el archivo que abre 'Open MCP config file' de Cascade; si ya existía el archivo antiguo ~/.codeium/windsurf/mcp_config.json también se actualiza."),
        Client("vscode", "VS Code (Copilot)", "servers", lambda s: _json_entry(s, typed=True),
               lambda e: [d / "mcp.json" for d in _vscode_user_dirs(e)[:1]], lambda d: d / ".vscode" / "mcp.json",
               lambda e: _which_any(e, "code") or _vscode_user_dirs(e)[0].is_dir(),
               optional_paths=lambda e: [_vscode_user_dirs(e)[1] / "mcp.json"] if _vscode_user_dirs(e)[1].is_dir() else []),
        Client("opencode", "OpenCode", "mcp", _opencode_entry,
               lambda e: [_opencode_path(e)], lambda d: d / "opencode.json",
               lambda e: _which_any(e, "opencode") or _opencode_path(e).parent.is_dir()),
        Client("antigravity", "Antigravity", "mcpServers", lambda s: _json_entry(s, typed=False),
               lambda e: [e.home / ".gemini" / "config" / "mcp_config.json"], lambda d: d / ".agents" / "mcp_config.json",
               lambda e: _which_any(e, "antigravity") or (e.home / ".gemini" / "config").is_dir() or (e.home / ".gemini" / "antigravity").is_dir()),
        Client("cline", "Cline", "mcpServers", lambda s: _json_entry(s, typed=False),
               _cline_paths, None,
               lambda e: _which_any(e, "cline") or any(p.parent.parent.is_dir() for p in _cline_paths(e)) or any(p.parent.parent.is_dir() for p in _cline_extension_paths(e)),
               optional_paths=lambda e: [p for p in _cline_extension_paths(e) if p.parent.parent.is_dir()],
               note="Se actualiza el ajuste del CLI de Cline y, si existe la carpeta de la extensión en VS Code, su archivo cline_mcp_settings.json."),
        Client("zed", "Zed", "context_servers", lambda s: {"command": s.command, "args": list(s.args), "env": dict(s.env)},
               lambda e: [_zed_path(e)], None, lambda e: _which_any(e, "zed") or _zed_path(e).parent.is_dir()),
    ]
    return {c.id: c for c in clients}


CLIENTS = _build_clients()


def resolve_ids(selection: list[str] | None, env: Env) -> tuple[list[str], list[str]]:
    """Returns (ids to install, ids skipped because they were not detected). `all` means the detected ones."""
    wanted = [x.strip().lower() for item in (selection or ["all"]) for x in item.split(",") if x.strip()]
    if not wanted or "all" in wanted:
        found = [cid for cid, c in CLIENTS.items() if c.detect(env)]
        return found, [cid for cid in CLIENTS if cid not in found]
    unknown = [w for w in wanted if w not in CLIENTS]
    if unknown:
        raise InstallError(f"Cliente desconocido: {', '.join(unknown)}. Próximo paso: usá uno de {', '.join(CLIENTS)} o `all`.")
    return wanted, []


def detected(env: Env) -> list[str]:
    return [cid for cid, c in CLIENTS.items() if c.detect(env)]


# --------------------------------------------------------------------------------------------- JSON (with comments)

def loads_lenient(text: str) -> tuple[Any, bool]:
    """Parses JSON that may carry // and /* */ comments or trailing commas. Returns (data, had_comments)."""
    out: list[str] = []
    i, n, commented = 0, len(text), False
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1]); i = j + 1
        elif text.startswith("//", i):
            commented = True
            while i < n and text[i] != "\n": i += 1
        elif text.startswith("/*", i):
            commented = True
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c); i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned or "{}"), commented


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() and os.name != "nt" else 0o600
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".meli-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(text); out.flush(); os.fsync(out.fileno())
        if os.name != "nt":
            os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _backup(path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-meli-{stamp}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bak-meli-{stamp}-{n}"); n += 1
    shutil.copy2(path, target)
    return target


def looks_like_ours(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    blob = " ".join([str(entry.get("command", "")), *map(str, entry.get("args", []) if isinstance(entry.get("args"), list) else [])])
    if isinstance(entry.get("command"), list):
        blob = " ".join(map(str, entry["command"]))
    return any(token in blob for token in OURS)


def _merge_entry(existing: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    merged = {**existing, **desired}
    for key in ("env", "environment"):
        if isinstance(existing.get(key), dict) and isinstance(desired.get(key), dict):
            merged[key] = {**existing[key], **desired[key]}
    return merged


@dataclass
class Outcome:
    client: str
    file: str
    status: str                      # created | added | updated | unchanged | conflict | error
    backup: str | None = None
    verified: bool = False
    warning: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _merge_json_file(client: Client, path: Path, spec: ServerSpec, *, dry_run: bool, force: bool) -> Outcome:
    desired = client.build(spec)  # type: ignore[misc]
    existed = path.exists()
    comments = False
    if existed:
        raw = path.read_text(encoding="utf-8-sig")
        try:
            data, comments = loads_lenient(raw)
        except ValueError:
            raise InstallError(f"{path} no es un JSON válido, así que no se tocó. Próximo paso: corregí o renombrá ese archivo (el instalador no lo pisa) y volvé a ejecutar el comando.") from None
        if not isinstance(data, dict):
            raise InstallError(f"{path} no tiene un objeto JSON en la raíz, así que no se tocó. Próximo paso: corregí ese archivo y reintentá.")
    else:
        data = {}
    container = data.get(client.container)
    if container is None:
        container = {}
    if not isinstance(container, dict):
        raise InstallError(f"En {path} la clave \"{client.container}\" no es un objeto, así que no se tocó. Próximo paso: corregí ese archivo y reintentá.")
    current = container.get(spec.name)
    if current is not None and not looks_like_ours(current) and not force:
        return Outcome(client.id, str(path), "conflict", detail=f"Ya hay un servidor distinto llamado \"{spec.name}\". Próximo paso: reintentá con --name otro-nombre, o con --force si querés reemplazarlo (se guarda copia de respaldo).")
    new_entry = _merge_entry(current, desired) if isinstance(current, dict) else desired
    if current == new_entry:
        return Outcome(client.id, str(path), "unchanged", verified=True)
    updated = copy.deepcopy(data)
    updated.setdefault(client.container, {})
    if not isinstance(updated[client.container], dict):
        updated[client.container] = {}
    updated[client.container][spec.name] = new_entry
    status = "created" if not existed else ("added" if current is None else "updated")
    if dry_run:
        return Outcome(client.id, str(path), status, detail="dry-run: no se escribió nada")
    backup = str(_backup(path)) if existed else None
    _write_atomic(path, json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    outcome = Outcome(client.id, str(path), status, backup=backup)
    if comments:
        outcome.warning = "El archivo tenía comentarios y se reescribió como JSON estándar sin ellos; la copia de respaldo los conserva."
    reread, _ = loads_lenient(path.read_text(encoding="utf-8"))
    outcome.verified = reread.get(client.container, {}).get(spec.name) == new_entry
    return outcome


# --------------------------------------------------------------------------------------------- TOML (Codex)

def _toml_block(spec: ServerSpec) -> str:
    q = json.dumps
    lines = [f"[mcp_servers.{spec.name}]", f"command = {q(spec.command)}", "args = [" + ", ".join(q(a) for a in spec.args) + "]", "startup_timeout_sec = 120"]
    if spec.env:
        lines += ["", f"[mcp_servers.{spec.name}.env]", *[f"{k} = {q(v)}" for k, v in spec.env.items()]]
    return "\n".join(lines) + "\n"


def _strip_toml_tables(text: str, name: str) -> str:
    header = re.compile(r'^\s*\[\s*mcp_servers\s*\.\s*(?:"%s"|%s)\s*(?:\..*)?\]\s*(?:#.*)?$' % (re.escape(name), re.escape(name)))
    any_header = re.compile(r"^\s*\[")
    kept, skipping = [], False
    for line in text.splitlines():
        if any_header.match(line):
            skipping = bool(header.match(line))
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip("\n")


def _merge_toml_file(client: Client, path: Path, spec: ServerSpec, *, dry_run: bool, force: bool) -> Outcome:
    existed = path.exists()
    text = path.read_text(encoding="utf-8-sig") if existed else ""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise InstallError(f"{path} no es un TOML válido, así que no se tocó. Próximo paso: corregí o renombrá ese archivo y volvé a ejecutar el comando.") from None
    current = (before.get("mcp_servers") or {}).get(spec.name) if isinstance(before.get("mcp_servers"), dict) else None
    if current is not None and not looks_like_ours(current) and not force:
        return Outcome(client.id, str(path), "conflict", detail=f"Ya hay un servidor distinto llamado \"{spec.name}\". Próximo paso: reintentá con --name otro-nombre o con --force.")
    stripped = _strip_toml_tables(text, spec.name)
    new_text = (stripped + "\n\n" if stripped else "") + _toml_block(spec)
    after = tomllib.loads(new_text)
    new_entry = after["mcp_servers"][spec.name]
    preserved = {k: v for k, v in (current or {}).items() if k not in new_entry}
    if preserved:  # keep user-added keys such as enabled = false
        extra = "\n".join(f"{k} = {json.dumps(v)}" for k, v in preserved.items() if isinstance(v, (str, int, float, bool)))
        if extra:
            new_text = new_text.replace(f"[mcp_servers.{spec.name}]\n", f"[mcp_servers.{spec.name}]\n{extra}\n", 1)
            after = tomllib.loads(new_text)
            new_entry = after["mcp_servers"][spec.name]
    if {k: v for k, v in before.items() if k != "mcp_servers"} != {k: v for k, v in after.items() if k != "mcp_servers"}:
        raise InstallError(f"No se pudo editar {path} sin alterar otros ajustes; no se tocó. Próximo paso: agregá a mano el bloque que muestra `mercadolibre-mcp install --dry-run --json` o reportalo.")
    if current == new_entry:
        return Outcome(client.id, str(path), "unchanged", verified=True)
    status = "created" if not existed else ("added" if current is None else "updated")
    if dry_run:
        return Outcome(client.id, str(path), status, detail="dry-run: no se escribió nada")
    backup = str(_backup(path)) if existed else None
    _write_atomic(path, new_text)
    reread = tomllib.loads(path.read_text(encoding="utf-8"))
    outcome = Outcome(client.id, str(path), status, backup=backup)
    outcome.verified = reread.get("mcp_servers", {}).get(spec.name) == new_entry
    return outcome


# --------------------------------------------------------------------------------------------- public API

def target_files(client: Client, env: Env, scope: str, project_dir: Path | None) -> list[Path]:
    if scope == "project":
        if client.project_path is None:
            raise InstallError(f"{client.label} no tiene configuración por proyecto documentada. Próximo paso: usá --scope user.")
        return [client.project_path((project_dir or env.cwd).resolve())]
    files = list(client.user_paths(env))
    if client.optional_paths:
        files += [p for p in client.optional_paths(env) if p.exists() and p not in files]
    return files


def install_client(client_id: str, spec: ServerSpec, env: Env, *, scope: str = "user", project_dir: Path | None = None, dry_run: bool = False, force: bool = False) -> list[Outcome]:
    client = CLIENTS[client_id]
    outcomes = []
    for path in target_files(client, env, scope, project_dir):
        try:
            if client.toml:
                outcomes.append(_merge_toml_file(client, path, spec, dry_run=dry_run, force=force))
            else:
                outcomes.append(_merge_json_file(client, path, spec, dry_run=dry_run, force=force))
        except InstallError as error:
            outcomes.append(Outcome(client_id, str(path), "error", detail=str(error)))
        except OSError as error:
            outcomes.append(Outcome(client_id, str(path), "error", detail=f"No se pudo escribir ({error.strerror or error}). Próximo paso: revisá los permisos de esa carpeta y reintentá."))
    return outcomes


def installed_status(spec_name: str, env: Env) -> dict[str, dict[str, Any]]:
    """For doctor: which detected clients already have our entry in their user config."""
    result: dict[str, dict[str, Any]] = {}
    for cid, client in CLIENTS.items():
        if not client.detect(env):
            continue
        found = False
        for path in target_files(client, env, "user", None):
            if not path.exists():
                continue
            with contextlib.suppress(Exception):
                if client.toml:
                    found = found or spec_name in (tomllib.loads(path.read_text(encoding="utf-8-sig")).get("mcp_servers") or {})
                else:
                    data, _ = loads_lenient(path.read_text(encoding="utf-8-sig"))
                    found = found or spec_name in (data.get(client.container) or {})
        result[cid] = {"label": client.label, "registered": found}
    return result


async def _handshake(spec: ServerSpec, timeout: float) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=spec.command, args=spec.args, env={**os.environ, **spec.env})
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errlog:
        async with asyncio.timeout(timeout):
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    return {"server": init.serverInfo.name, "version": init.serverInfo.version, "tools": len(tools.tools), "has_start_here": any(t.name == "meli_start_here" for t in tools.tools)}


def verify_launch(spec: ServerSpec, timeout: float = 240) -> dict[str, Any]:
    """Starts the server exactly as the client will and performs the MCP handshake (also warms the uvx cache)."""
    try:
        info = asyncio.run(_handshake(spec, timeout))
    except Exception as error:  # noqa: BLE001 - reported, never raised
        return {"ok": False, "error": f"No arrancó el servidor ({type(error).__name__}). Próximo paso: ejecutá `{' '.join(spec.argv)}` en una terminal para ver el error y revisá que uv y Git estén instalados."}
    return {"ok": True, **info}

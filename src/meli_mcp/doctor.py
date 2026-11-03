"""`doctor`: diagnoses the installation and always ends with the next concrete step."""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Any

from . import __version__
from .authflow import HOSTS
from .client import CLI, MeliClient, MeliError, access_mode, load_private, state_dir
from . import installer


def _check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str, next_step: str | None = None, level: str = "error") -> None:
    checks.append({"check": name, "ok": ok, "level": "ok" if ok else level, "detail": detail, **({"next_step": next_step} if next_step and not ok else {})})


def run(offline: bool = False, env: installer.Env | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    _check(checks, "version", True, f"{CLI} {__version__} (Python {sys.version_info.major}.{sys.version_info.minor})")
    runner = shutil.which("uvx")
    _check(checks, "uv", bool(runner), f"uvx en {runner}" if runner else "uv no está en el PATH.", "Instalá uv (docs/AGENT_INSTALL.md, paso 1) y abrí una terminal nueva.", "warn")
    path = state_dir()
    private = True
    if path.exists() and os.name != "nt":
        private = (path.stat().st_mode & 0o077) == 0
    _check(checks, "carpeta_de_estado", private, "carpeta privada" if path.exists() else "todavía no existe (se crea al ejecutar setup)", f"Ejecutá `chmod 700 {path}`.", "warn")
    config: dict[str, Any] | None = None
    try:
        config = load_private("config.json")
        missing = [k for k in ("client_id", "client_secret", "redirect_uri", "country") if not config.get(k)]
        if missing:
            _check(checks, "configuracion", False, f"Faltan campos: {', '.join(missing)}.", f"Ejecutá `{CLI} setup` con todos los datos de la app (docs/AGENT_INSTALL.md, paso 5).")
        elif config["country"] not in HOSTS or not str(config["redirect_uri"]).startswith("https://"):
            _check(checks, "configuracion", False, "País o URL de redirección inválidos.", f"Ejecutá `{CLI} setup` otra vez con --country y --redirect-uri correctos.")
        else:
            _check(checks, "configuracion", True, f"app {config['client_id']}, país {config['country']}, PKCE {'sí' if config.get('pkce') else 'no'}, redirección {config['redirect_uri']}")
    except MeliError:
        _check(checks, "configuracion", False, "No hay configuración de la app.", f"Creá la app en el DevCenter (`{CLI} devcenter --country ar`) y ejecutá `{CLI} setup`.")
    token: dict[str, Any] | None = None
    try:
        token = load_private("token.json")
        if not token.get("refresh_token"):
            _check(checks, "autorizacion", False, "La autorización no trae refresh token.", f"Habilitá offline_access en la app y ejecutá `{CLI} authorize`.")
        else:
            left = float(token.get("expires_at", 0)) - time.time()
            _check(checks, "autorizacion", True, "cuenta autorizada" + (" (el acceso se renueva solo)" if left < 60 else f" (acceso vigente {int(left // 60)} min más, se renueva solo)"))
            scope = token.get("scope")
            if isinstance(scope, str):
                have = set(scope.replace(",", " ").split())
                lacking = [s for s in ("read", "write", "offline_access") if s not in have]
                _check(checks, "permisos", not lacking, f"permisos autorizados: {', '.join(sorted(have))}" if not lacking else f"Faltan permisos: {', '.join(lacking)}.",
                       "En el DevCenter tildá \"Lectura y escritura\" en todos los grupos (y acceso offline) y ejecutá `authorize` otra vez.")
    except MeliError:
        _check(checks, "autorizacion", False, "La cuenta todavía no está autorizada.", f"Ejecutá `{CLI} authorize` (docs/AGENT_INSTALL.md, paso 6).")
    mode = access_mode()
    _check(checks, "modo", True, "lectura y escritura (completo)" if mode == "full" else "SOLO LECTURA (las escrituras están bloqueadas; `mode full` las habilita)")
    if not offline and config is not None and token is not None:
        try:
            profile = MeliClient().request("GET", "/users/me")
            data = profile.get("data", profile) if isinstance(profile, dict) else {}
            _check(checks, "api", True, f"Mercado Libre responde: usuario {data.get('nickname', '?')} (id {data.get('id', '?')})")
        except MeliError as error:
            _check(checks, "api", False, str(error), f"Seguí el próximo paso del mensaje; si dice 401 o 403 ejecutá `{CLI} authorize`.")
    env = env or installer.Env.current()
    status = installer.installed_status(installer.DEFAULT_NAME, env)
    registered = [v["label"] for v in status.values() if v["registered"]]
    if status:
        _check(checks, "clientes_mcp", bool(registered), f"registrado en: {', '.join(registered)}" if registered else f"Clientes detectados sin registrar: {', '.join(v['label'] for v in status.values())}.",
               f"Ejecutá `{CLI} install --client all` y reiniciá el cliente.", "warn")
    else:
        _check(checks, "clientes_mcp", False, "No se detectó ningún cliente MCP conocido.", f"Instalá/abrí tu cliente de IA y ejecutá `{CLI} install --client <nombre>` (nombres en docs/AGENT_INSTALL.md).", "warn")
    errors = [c for c in checks if c["level"] == "error"]
    warns = [c for c in checks if c["level"] == "warn"]
    first = (errors or warns or [{}])[0]
    return {"ok": not errors, "version": __version__, "checks": checks, "next_step": first.get("next_step") if (errors or warns) else "Todo listo: reiniciá tu cliente de IA y pedile que llame meli_start_here y meli_status."}

"""Command line: non-interactive setup, authorization, client installation, diagnosis and the server itself.

Every command that an AI agent may run accepts flags, environment variables or stdin and can print JSON (`--json`).
Secrets never enter tool descriptions, tool arguments, logs or summaries.
"""
from __future__ import annotations
import argparse
import getpass
import json
import logging
import os
import secrets  # noqa: F401 - module attribute used by tests that pin the state/verifier
import sys
import webbrowser
from pathlib import Path
from typing import Any
from . import __version__, authflow, installer
from .authflow import DEFAULT_REDIRECT, DEVCENTER, HOSTS
from .client import CLI, MeliError, MeliClient, ensure_state_dir, load_private, token_lock, write_private

COUNTRIES = "/".join(HOSTS)


def _emit(data: dict[str, Any], as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))


def _truthy(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "y", "s", "si", "sí", "on"}


# --------------------------------------------------------------------------------------------- setup

def setup(*, client_id: str | None = None, client_secret: str | None = None, redirect_uri: str | None = None, country: str | None = None,
          pkce: bool | None = None, mode: str | None = None, as_json: bool = False, interactive: bool | None = None) -> dict[str, Any]:
    """Saves the app credentials. Values come from arguments, then MELI_* environment variables, then (only on a terminal) prompts."""
    env = os.environ
    client_id = client_id or env.get("MELI_CLIENT_ID")
    client_secret = client_secret or env.get("MELI_CLIENT_SECRET")
    redirect_uri = redirect_uri or env.get("MELI_REDIRECT_URI")
    country = country or env.get("MELI_COUNTRY")
    pkce = pkce if pkce is not None else _truthy(env.get("MELI_PKCE"))
    interactive = sys.stdin.isatty() if interactive is None else interactive
    missing = [name for name, value in (("--client-id", client_id), ("--client-secret (o --client-secret-stdin)", client_secret)) if not value]
    if missing and not interactive:
        raise MeliError(f"Faltan datos de la app: {', '.join(missing)}. Próximo paso: pedile al usuario el APP ID y el Client Secret (guía: `{CLI} devcenter --country ar`) y ejecutá `{CLI} setup --client-id <APP_ID> --client-secret-stdin --redirect-uri {DEFAULT_REDIRECT} --country ar`, pasando el secreto por stdin.")
    if interactive:
        if not client_id: client_id = input("APP ID: ").strip()
        if not client_secret: client_secret = getpass.getpass("Secret Key (no se mostrará): ").strip()
        if not redirect_uri: redirect_uri = input(f"Redirect URI HTTPS exacta configurada en la aplicación [{DEFAULT_REDIRECT}]: ").strip() or DEFAULT_REDIRECT
        if not country: country = input(f"País ({COUNTRIES}) [ar]: ").strip().lower() or "ar"
        if pkce is None: pkce = input("¿Activaste PKCE en la aplicación? [S/n]: ").strip().lower() not in {"n", "no"}
    redirect_uri = (redirect_uri or DEFAULT_REDIRECT).strip()
    country = (country or "ar").strip().lower()
    pkce = True if pkce is None else pkce
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not client_id or not client_secret or country not in HOSTS:
        raise MeliError(f"Datos inválidos: el APP ID y el Secret no pueden estar vacíos y el país debe ser uno de {COUNTRIES}. Próximo paso: corregí el dato y ejecutá `{CLI} setup` otra vez.")
    authflow.validate_redirect(redirect_uri)
    if mode is not None and mode not in {"full", "read_only"}:
        raise MeliError("El modo debe ser `full` o `read-only`. Próximo paso: corregilo.")
    with token_lock():
        try:
            previous = load_private("config.json")
        except MeliError:
            previous = {}
        config = {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "country": country, "pkce": pkce}
        chosen_mode = mode or previous.get("mode")
        if chosen_mode:
            config["mode"] = chosen_mode
        write_private(ensure_state_dir() / "config.json", config)
        # Credentials from another app must never stay silently bound to the new configuration.
        removed = bool(previous) and previous.get("client_id") != client_id and (ensure_state_dir() / "token.json").exists()
        if removed:
            (ensure_state_dir() / "token.json").unlink(missing_ok=True)
    loopback = authflow.loopback_target(redirect_uri) is not None
    result = {"ok": True, "country": country, "redirect_uri": redirect_uri, "pkce": pkce, "mode": chosen_mode or "full", "authorization_removed": removed,
              "next_step": f"Ejecutá `{CLI} authorize`" + (" (abre el navegador y captura el permiso solo)." if loopback else " (abre el navegador; después pegá la URL de retorno o usá --start/--finish).")}
    _emit(result, as_json, ["Configuración guardada (el secreto no se muestra).", "Próximo paso: " + result["next_step"]])
    return result


# --------------------------------------------------------------------------------------------- authorize

def authorize(*, start: bool = False, finish: bool = False, callback_url: str | None = None, code: str | None = None, listen: bool | None = None,
              open_browser: bool = True, timeout: float = 300, as_json: bool = False, ready: threading.Event | None = None) -> dict[str, Any]:
    """Authorizes the account. Default: listens on https://localhost when the redirect URI points there, else asks for the return URL."""
    config = load_private("config.json")
    redirect = config["redirect_uri"]

    def done() -> dict[str, Any]:
        result = {"ok": True, "authorized": True, "next_step": f"Ejecutá `{CLI} doctor` y reiniciá tu cliente de IA."}
        _emit(result, as_json, ["Cuenta autorizada. Podés cerrar la ventana del navegador.", "Próximo paso: " + result["next_step"]])
        return result

    if finish:
        pending = authflow.load_pending(config)
        text = callback_url or code
        if not text:
            text = getpass.getpass("Pega la URL de retorno o el código (no se mostrará): ").strip()
        authflow.exchange_code(config, authflow.extract_code(text, redirect, pending["state"]), pending["verifier"])
        return done()

    state, verifier = authflow.new_attempt()
    url = authflow.build_url(config, state, verifier)
    target = authflow.loopback_target(redirect)
    use_listener = (target is not None) if listen is None else listen
    if start:
        authflow.save_pending(config, state, verifier)
        opened = webbrowser.open(url) if open_browser else False
        result = {"ok": True, "authorize_url": url, "browser_opened": bool(opened), "redirect_uri": redirect, "expires_in": authflow.PENDING_TTL,
                  "next_step": f"Abrí authorize_url (si no se abrió), pedile al usuario que inicie sesión con la cuenta principal y autorice, y que te pase la URL completa de la barra de direcciones; luego ejecutá `{CLI} authorize --finish --callback-url '<URL>'`."}
        _emit(result, as_json, ["Abrí este enlace, inicia sesión con la cuenta principal y autoriza la aplicación:", url, "", "Luego copiá la URL completa de la barra de direcciones (aunque la página no cargue) y ejecutá:", f"  {CLI} authorize --finish --callback-url '<URL>'"])
        return result
    print("Abrí este enlace, iniciá sesión con la cuenta principal y autorizá la aplicación:\n" + url, file=sys.stderr)
    if use_listener:
        print("El navegador va a mostrar una advertencia de certificado para localhost: es normal; tocá Avanzado y Continuar.", file=sys.stderr)
        if open_browser: webbrowser.open(url)
        returned = authflow.listen_for_callback(redirect, tls=True, timeout=timeout, ready=ready)
    else:
        print("Luego copiá la URL completa de la barra de direcciones, aunque la página no cargue.", file=sys.stderr)
        if open_browser: webbrowser.open(url)
        returned = getpass.getpass("Pega la URL de retorno (no se mostrará): ").strip()
    authflow.exchange_code(config, authflow.extract_code(returned, redirect, state), verifier)
    return done()


# --------------------------------------------------------------------------------------------- mode / devcenter / token

def set_mode(mode: str | None, as_json: bool = False) -> dict[str, Any]:
    with token_lock():
        config = load_private("config.json")
        if mode is not None:
            config["mode"] = "read_only" if mode == "read-only" else "full"
            write_private(ensure_state_dir() / "config.json", config)
    current = "read_only" if str(config.get("mode", "full")) == "read_only" else "full"
    note = "Reiniciá tu cliente de IA para que el cambio se aplique." if mode is not None else "Para cambiarlo: `mode full` o `mode read-only`."
    result = {"ok": True, "mode": current, "note": note}
    _emit(result, as_json, [f"Modo: {'SOLO LECTURA' if current == 'read_only' else 'lectura y escritura (completo)'}. {note}"])
    return result


def devcenter(country: str, redirect: str, open_it: bool, as_json: bool) -> dict[str, Any]:
    if country not in DEVCENTER:
        raise MeliError(f"País inválido. Próximo paso: usá uno de {COUNTRIES}.")
    from .appguide import app_checklist
    guide = app_checklist(country, redirect)
    if open_it:
        webbrowser.open(guide["devcenter_url"])
    lines = [f"DevCenter: {guide['devcenter_url']}", "", *guide["antes_de_empezar"], "", *guide["pasos"], "", "Si el formulario rechaza la URI de redirección:", *guide["si_el_formulario_rechaza_la_uri"], "", "Copiá: " + " y ".join(guide["valores_a_copiar"]), "", guide["nota_localhost"]]
    _emit({"ok": True, **guide}, as_json, lines)
    return guide


def http_token(rotate: bool, public_url: str | None, as_json: bool) -> dict[str, Any]:
    from . import httpserve
    token = httpserve.get_token(rotate)
    base = (public_url or "").rstrip("/")
    result: dict[str, Any] = {"ok": True, "token": token, "bearer_header": f"Authorization: Bearer {token}", "local_url": "http://127.0.0.1:8765/mcp"}
    if base:
        result["connector_url"] = f"{base}/t/{token}/mcp"
    lines = [f"Token: {token}", "Cabecera: Authorization: Bearer <token>", "URL local: http://127.0.0.1:8765/mcp (arrancá el servidor con `serve --transport http`)"]
    if base:
        lines.append(f"URL para ChatGPT: {result['connector_url']}")
    _emit(result, as_json, lines)
    return result


# --------------------------------------------------------------------------------------------- install

def install(args: argparse.Namespace) -> dict[str, Any]:
    env = installer.Env.current()
    ids, skipped_ids = installer.resolve_ids(args.client, env)
    spec = installer.default_spec(env, name=args.name, source=args.source, command=args.exe, local=args.local)
    outcomes: list[installer.Outcome] = []
    for cid in ids:
        outcomes.extend(installer.install_client(cid, spec, env, scope=args.scope, project_dir=Path(args.project_dir) if args.project_dir else None, dry_run=args.dry_run, force=args.force))
    changed = [o for o in outcomes if o.status in {"created", "added", "updated", "unchanged"}]
    problems = [o for o in outcomes if o.status in {"error", "conflict"}]
    launch: dict[str, Any] | None = None
    if changed and not args.dry_run and not args.no_verify_launch:
        launch = installer.verify_launch(spec)
    next_steps: list[str] = []
    if not ids:
        next_steps.append(f"No se detectó ningún cliente MCP. Preguntale al usuario qué usa y ejecutá `{CLI} install --client <nombre>` (nombres: {', '.join(installer.CLIENTS)}).")
    for o in problems:
        next_steps.append(o.detail or "Revisá el error.")
    if changed and not args.dry_run:
        next_steps.append("Pedile al usuario que cierre y vuelva a abrir por completo su cliente de IA y probá llamando a la tool meli_start_here y luego meli_status.")
    if launch is not None and not launch["ok"]:
        next_steps.append(launch["error"])
    ok = bool(changed) and not problems and (launch is None or launch["ok"]) and all(o.verified or args.dry_run for o in changed)
    result = {"ok": ok, "dry_run": args.dry_run, "command": spec.argv, "installed": [o.as_dict() for o in changed], "problems": [o.as_dict() for o in problems],
              "not_detected": skipped_ids, "launch_check": launch, "next_steps": next_steps}
    lines = [f"{'[dry-run] ' if args.dry_run else ''}Servidor: {' '.join(spec.argv)}"]
    for o in outcomes:
        lines.append(f"- {installer.CLIENTS[o.client].label}: {o.status} ({o.file})" + (f" copia de respaldo: {o.backup}" if o.backup else "") + (f" AVISO: {o.warning}" if o.warning else "") + (f" {o.detail}" if o.detail and o.status in {'error', 'conflict'} else ""))
    if launch is not None:
        lines.append(f"- Arranque del servidor: {'OK, ' + str(launch['tools']) + ' tools (versión ' + str(launch['version']) + ')' if launch['ok'] else launch['error']}")
    if skipped_ids and not args.client:
        lines.append("No detectados: " + ", ".join(skipped_ids))
    lines += ["Próximos pasos:", *[f"  {s}" for s in next_steps]]
    _emit(result, args.json, lines)
    if not ok:
        raise SystemExit(1)
    return result


# --------------------------------------------------------------------------------------------- entry point

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=CLI, description="Servidor MCP de Mercado Libre y su instalación guiada (docs/AGENT_INSTALL.md)")
    parser.add_argument("--version", action="version", version=f"{CLI} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="Guarda las credenciales de la app (flags, variables MELI_* o stdin; sin preguntas obligatorias)")
    p.add_argument("--client-id"); p.add_argument("--client-secret", help="visible en la lista de procesos; preferí --client-secret-stdin")
    p.add_argument("--client-secret-stdin", action="store_true", help="lee el Client Secret desde la entrada estándar")
    p.add_argument("--redirect-uri"); p.add_argument("--country", choices=sorted(HOSTS))
    p.add_argument("--pkce", dest="pkce", action="store_true", default=None); p.add_argument("--no-pkce", dest="pkce", action="store_false")
    p.add_argument("--mode", choices=["full", "read-only"], help="full (por defecto): lectura y escritura; read-only: solo consultas")
    p.add_argument("--json", action="store_true")

    for name in ("authorize", "login"):
        p = sub.add_parser(name, help="Autoriza la cuenta (captura el permiso en localhost o con --start/--finish)")
        p.add_argument("--start", action="store_true", help="solo inicia: imprime la URL y guarda el intento")
        p.add_argument("--finish", action="store_true", help="termina con --callback-url o --code")
        p.add_argument("--callback-url"); p.add_argument("--code")
        p.add_argument("--no-listen", action="store_true", help="no abrir el receptor local; pedir la URL de retorno")
        p.add_argument("--no-open", action="store_true", help="no abrir el navegador")
        p.add_argument("--timeout", type=float, default=300)
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("serve", help="Arranca el servidor MCP (stdio por defecto)")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8765)
    p.add_argument("--allowed-host", action="append", default=[], help="dominio del túnel permitido (solo http)")

    sub.add_parser("check", help="Consulta la cuenta conectada")
    p = sub.add_parser("doctor", help="Diagnostica la instalación y dice el próximo paso")
    p.add_argument("--json", action="store_true"); p.add_argument("--offline", action="store_true")

    p = sub.add_parser("mode", help="Muestra o cambia el modo: full (lectura y escritura) o read-only")
    p.add_argument("mode", nargs="?", choices=["full", "read-only"]); p.add_argument("--json", action="store_true")

    p = sub.add_parser("install", help="Registra el servidor en tus clientes de IA (backup, merge, idempotente, verificación)")
    p.add_argument("--client", action="append", help=f"all (detectados) o uno o varios de: {', '.join(installer.CLIENTS)}")
    p.add_argument("--scope", choices=["user", "project"], default="user"); p.add_argument("--project-dir")
    p.add_argument("--name", default=installer.DEFAULT_NAME); p.add_argument("--source", default=installer.DEFAULT_SOURCE)
    p.add_argument("--command", dest="exe", help="ejecutable propio (se registra `<ejecutable> serve`)"); p.add_argument("--local", action="store_true", help="usa este Python (`python -m meli_mcp serve`)")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true"); p.add_argument("--no-verify-launch", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("devcenter", help="Guía exacta para crear la app en el DevCenter")
    p.add_argument("--country", default="ar", choices=sorted(HOSTS)); p.add_argument("--redirect-uri", default=DEFAULT_REDIRECT)
    p.add_argument("--open", action="store_true"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("http-token", help="Muestra (o rota) el token del transporte HTTP")
    p.add_argument("--rotate", action="store_true"); p.add_argument("--public-url"); p.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    try:
        if args.command == "setup":
            secret = args.client_secret
            if args.client_secret_stdin:
                secret = sys.stdin.read().strip()
            setup(client_id=args.client_id, client_secret=secret, redirect_uri=args.redirect_uri, country=args.country, pkce=args.pkce,
                  mode={"read-only": "read_only"}.get(args.mode, args.mode), as_json=as_json)
        elif args.command in ("authorize", "login"):
            if args.finish and not (args.callback_url or args.code) and not sys.stdin.isatty():
                raise MeliError(f"Falta la URL de retorno. Próximo paso: ejecutá `{CLI} authorize --finish --callback-url '<URL completa>'`.")
            authorize(start=args.start, finish=args.finish, callback_url=args.callback_url, code=args.code, listen=False if args.no_listen else None,
                      open_browser=not args.no_open, timeout=args.timeout, as_json=as_json)
        elif args.command == "check":
            result = MeliClient().request("GET", "/users/me")
            print(f"Conectado: usuario {result.get('nickname', '(sin nombre)')}, ID {result.get('id', '(desconocido)')}.")
        elif args.command == "doctor":
            from . import doctor
            report = doctor.run(offline=args.offline)
            if as_json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(f"{CLI} {report['version']}")
                for c in report["checks"]:
                    print(f"[{c['level'].upper():5}] {c['check']}: {c['detail']}")
                print("\nPróximo paso: " + str(report["next_step"]))
            sys.exit(0 if report["ok"] else 1)
        elif args.command == "mode":
            set_mode(args.mode, as_json)
        elif args.command == "install":
            install(args)
        elif args.command == "devcenter":
            devcenter(args.country, args.redirect_uri, args.open, as_json)
        elif args.command == "http-token":
            http_token(args.rotate, args.public_url, as_json)
        else:
            # HTTPX INFO logs contain request URLs and query parameters.
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            from .server import mcp
            if args.transport == "http":
                from . import httpserve
                httpserve.serve(mcp, args.host, args.port, args.allowed_host)
            else:
                mcp.run(transport="stdio")
    except (MeliError, installer.InstallError) as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(str(exc), file=sys.stderr)
        sys.exit(1)

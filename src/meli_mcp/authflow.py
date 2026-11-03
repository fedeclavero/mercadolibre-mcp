"""OAuth helpers: authorization URL, pending-state file, callback capture and code exchange.

The authorization URL, parameters, PKCE (S256) and the token exchange follow the official page
"Autenticación y Autorización". The redirect URI must be HTTPS and identical to the one registered in the app.
Nothing here prints or logs a code or a token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import ipaddress
import os
import secrets
import ssl
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .client import CLI, MeliClient, MeliError, ensure_state_dir, load_private, token_lock, write_private

# Authorization host per country: the docs say to swap ".com.ar" for the country's domain.
HOSTS = {"ar": "auth.mercadolibre.com.ar", "br": "auth.mercadolivre.com.br", "mx": "auth.mercadolibre.com.mx", "cl": "auth.mercadolibre.cl",
         "co": "auth.mercadolibre.com.co", "uy": "auth.mercadolibre.com.uy", "pe": "auth.mercadolibre.com.pe", "ec": "auth.mercadolibre.com.ec", "ve": "auth.mercadolibre.com.ve"}
# "Mis aplicaciones" (DevCenter) per country, as linked by the official page "Crea una aplicación".
DEVCENTER = {"ar": "https://developers.mercadolibre.com.ar/devcenter/", "br": "https://developers.mercadolivre.com.br/devcenter/", "mx": "https://developers.mercadolibre.com.mx/devcenter/",
             "cl": "https://developers.mercadolibre.cl/devcenter/", "co": "https://developers.mercadolibre.com.co/devcenter/", "uy": "https://developers.mercadolibre.com.uy/devcenter/",
             "pe": "https://developers.mercadolibre.com.pe/devcenter/", "ec": "https://developers.mercadolibre.com.ec/devcenter/", "ve": "https://developers.mercadolibre.com.ve/devcenter/"}
DEFAULT_REDIRECT = "https://localhost:8765/callback"
PENDING_FILE = "pending-auth.json"
PENDING_TTL = 900


def validate_redirect(redirect: str) -> None:
    uri = urlsplit(redirect)
    if uri.scheme != "https" or not uri.netloc or uri.fragment or uri.username or uri.password:
        raise MeliError("La URL de redirección debe ser HTTPS, exacta y sin usuario ni fragmento (el DevCenter exige HTTPS). Próximo paso: usá `https://localhost:8765/callback` o la que registraste en la app.")


def loopback_target(redirect: str) -> tuple[str, int, str] | None:
    """(host, port, path) when the redirect URI points to this computer, else None."""
    uri = urlsplit(redirect)
    host = uri.hostname or ""
    try:
        is_loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        return None
    return "127.0.0.1", uri.port or 443, uri.path or "/"


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def build_url(config: dict[str, Any], state: str, verifier: str) -> str:
    params = {"response_type": "code", "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "state": state}
    if config.get("pkce"):
        params["code_challenge"] = pkce_challenge(verifier)
        params["code_challenge_method"] = "S256"
    return "https://" + HOSTS[config["country"]] + "/authorization?" + urlencode(params)


def new_attempt() -> tuple[str, str]:
    return secrets.token_urlsafe(32), secrets.token_urlsafe(64)


def save_pending(config: dict[str, Any], state: str, verifier: str) -> None:
    with token_lock():
        write_private(ensure_state_dir() / PENDING_FILE, {"state": state, "verifier": verifier, "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "created_at": time.time()})


def load_pending(config: dict[str, Any]) -> dict[str, Any]:
    try:
        pending = load_private(PENDING_FILE)
    except MeliError:
        raise MeliError(f"No hay una autorización iniciada. Próximo paso: ejecutá `{CLI} authorize --start` y luego `authorize --finish`.") from None
    if time.time() - float(pending.get("created_at", 0)) > PENDING_TTL or pending.get("client_id") != config.get("client_id") or pending.get("redirect_uri") != config.get("redirect_uri"):
        clear_pending()
        raise MeliError(f"La autorización iniciada venció o es de otra configuración. Próximo paso: ejecutá `{CLI} authorize --start` otra vez.")
    return pending


def clear_pending() -> None:
    (ensure_state_dir() / PENDING_FILE).unlink(missing_ok=True)


def extract_code(callback: str, redirect_uri: str, state: str | None) -> str:
    """Pulls the code from the full return URL (state checked) or accepts a bare code when no state is available."""
    text = callback.strip()
    if "://" not in text and "code=" not in text:
        if not text or any(c.isspace() for c in text):
            raise MeliError("El código es inválido. Próximo paso: pegá la URL completa de la barra de direcciones.")
        return text
    redirected = urlsplit(text)
    expected = urlsplit(redirect_uri)
    if (redirected.scheme, redirected.netloc, redirected.path) != (expected.scheme, expected.netloc, expected.path):
        raise MeliError("La URL de retorno no corresponde a la configurada. Próximo paso: copiá la URL completa que quedó en la barra del navegador después de autorizar (empieza con la URL de redirección).")
    values = parse_qs(redirected.query, keep_blank_values=True)
    if len(values.get("state", [])) != 1 or state is None or not hmac.compare_digest(values["state"][0], state) or len(values.get("code", [])) != 1 or not values["code"][0]:
        raise MeliError(f"No se recibió un código válido o el state no coincide. Próximo paso: ejecutá `{CLI} authorize` otra vez y usá la URL de esa vuelta.")
    return values["code"][0]


def exchange_code(config: dict[str, Any], code: str, verifier: str) -> dict[str, Any]:
    payload = {"grant_type": "authorization_code", "client_id": config["client_id"], "client_secret": config["client_secret"], "redirect_uri": config["redirect_uri"], "code": code}
    if config.get("pkce"):
        payload["code_verifier"] = verifier
    try:
        with token_lock():
            if load_private("config.json") != config:
                raise MeliError(f"La configuración cambió durante la autorización. Próximo paso: ejecutá `{CLI} authorize` otra vez.")
            with MeliClient()._client() as client:
                response = client.post("/oauth/token", data=payload)
            if response.is_error:
                raise MeliError(f"Mercado Libre rechazó la autorización (HTTP {response.status_code}). Próximo paso: verificá que la URL de redirección de la app sea exactamente {config['redirect_uri']}, que el APP ID y el Secret sean los de esa app y que hayas entrado con la cuenta principal; después ejecutá `{CLI} authorize` otra vez.")
            token = response.json()
            if not token.get("access_token") or not token.get("refresh_token"):
                raise MeliError(f"No se recibió refresh token. Próximo paso: en el DevCenter habilitá el acceso offline (offline_access) de la app y ejecutá `{CLI} authorize` otra vez.")
            token["expires_at"] = time.time() + int(token["expires_in"])
            write_private(ensure_state_dir() / "token.json", token)
    except (httpx.HTTPError, KeyError, ValueError):
        raise MeliError(f"Falló el intercambio OAuth. Próximo paso: ejecutá `{CLI} authorize` otra vez.") from None
    clear_pending()
    return token


def _self_signed_context() -> ssl.SSLContext:
    """In-memory-lifetime certificate for https://localhost; the browser will warn once and the user proceeds."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    with tempfile.TemporaryDirectory() as folder:
        key_file, cert_file = Path(folder) / "k.pem", Path(folder) / "c.pem"
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        context.load_cert_chain(cert_file, key_file)
    return context


_DONE_PAGE = ("<!doctype html><meta charset='utf-8'><title>Mercado Libre MCP</title>"
              "<body style='font-family:sans-serif;max-width:32em;margin:4em auto'><h2>Listo</h2><p>La cuenta quedó autorizada. Ya podés cerrar esta ventana y volver al asistente.</p></body>")
_ERROR_PAGE = ("<!doctype html><meta charset='utf-8'><title>Mercado Libre MCP</title>"
               "<body style='font-family:sans-serif;max-width:32em;margin:4em auto'><h2>No se pudo autorizar</h2><p>Volvé al asistente y ejecutá la autorización otra vez.</p></body>")


def listen_for_callback(redirect_uri: str, *, tls: bool = True, timeout: float = 300, ready: threading.Event | None = None) -> str:
    """Serves the redirect URI on this computer and returns the full return URL (with code and state)."""
    target = loopback_target(redirect_uri)
    if target is None:
        raise MeliError("Esa URL de redirección no apunta a este equipo. Próximo paso: usá `authorize --start` y `authorize --finish --callback-url <URL>`.")
    host, port, path = target
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path != path:
                self.send_response(404); self.end_headers(); return
            query = parse_qs(parts.query)
            page = _DONE_PAGE if "code" in query and "error" not in query else _ERROR_PAGE
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            captured["url"] = f"{'https' if tls else 'http'}://{self.headers.get('Host', f'localhost:{port}')}{self.path}"

        def log_message(self, *args: Any) -> None:  # the request line carries the code
            return

    try:
        server = http.server.HTTPServer((host, port), Handler)
    except OSError:
        raise MeliError(f"No se pudo abrir el puerto {port} en este equipo (¿está ocupado?). Próximo paso: cerrá lo que lo usa o registrá en la app otra URL de redirección con otro puerto y ejecutá `{CLI} setup` con ella.") from None
    if tls:
        server.socket = _self_signed_context().wrap_socket(server.socket, server_side=True)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    if ready is not None:
        ready.set()
    try:
        while "url" not in captured and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "url" not in captured:
        raise MeliError(f"Pasaron {int(timeout)} segundos sin recibir la autorización. Próximo paso: ejecutá `{CLI} authorize` otra vez y completá el permiso en el navegador; si el navegador no puede abrir la URL local, usá `authorize --start` y `--finish`.")
    return captured["url"]

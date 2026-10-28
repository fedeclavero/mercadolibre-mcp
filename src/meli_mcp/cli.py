"""Interactive, local OAuth setup; secrets never enter tool descriptions or arguments."""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import hmac
import logging
import secrets
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit
import httpx
from .client import MeliError, MeliClient, ensure_state_dir, load_private, token_lock, write_private

HOSTS = {"ar": "auth.mercadolibre.com.ar", "br": "auth.mercadolivre.com.br", "mx": "auth.mercadolibre.com.mx", "cl": "auth.mercadolibre.cl", "co": "auth.mercadolibre.com.co", "uy": "auth.mercadolibre.com.uy", "pe": "auth.mercadolibre.com.pe"}

def setup() -> None:
    print("Configura una aplicación propia de Mercado Libre con los permisos mínimos necesarios y offline_access para renovación.")
    client_id = input("APP ID: ").strip()
    secret = getpass.getpass("Secret Key (no se mostrará): ").strip()
    redirect = input("Redirect URI HTTPS exacta configurada en la aplicación: ").strip()
    country = input("País (ar/br/mx/cl/co/uy/pe): ").strip().lower()
    pkce = input("¿Activaste PKCE en la aplicación? [s/N]: ").strip().lower() in {"s", "si", "sí", "y", "yes"}
    uri = urlsplit(redirect)
    if not client_id or not secret or uri.scheme != "https" or not uri.netloc or uri.fragment or uri.username or uri.password or country not in HOSTS:
        raise MeliError("Datos inválidos. La Redirect URI debe ser HTTPS y el país uno de la lista.")
    with token_lock():
        write_private(ensure_state_dir() / "config.json", {"client_id": client_id, "client_secret": secret, "redirect_uri": redirect, "country": country, "pkce": pkce})
        # Never leave credentials from another app silently bound to the new configuration.
        (ensure_state_dir() / "token.json").unlink(missing_ok=True)
    print("Configuración guardada. Ejecuta el comando authorize.")

def authorize() -> None:
    config = load_private("config.json")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    params = {"response_type": "code", "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "state": state}
    if config.get("pkce"):
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        params["code_challenge_method"] = "S256"
    url = "https://" + HOSTS[config["country"]] + "/authorization?" + urlencode(params)
    print("Abre este enlace, inicia sesión con la cuenta principal y autoriza la aplicación:\n" + url)
    print("Luego copia la URL completa de la barra de direcciones, aunque la página no cargue.")
    webbrowser.open(url)
    result = getpass.getpass("Pega la URL de retorno (no se mostrará): ").strip()
    redirected = urlsplit(result)
    expected = urlsplit(config["redirect_uri"])
    if (redirected.scheme, redirected.netloc, redirected.path) != (expected.scheme, expected.netloc, expected.path):
        raise MeliError("La URL de retorno no corresponde a la configurada.")
    values = parse_qs(redirected.query, keep_blank_values=True)
    if len(values.get("state", [])) != 1 or not hmac.compare_digest(values["state"][0], state) or len(values.get("code", [])) != 1 or not values["code"][0]:
        raise MeliError("No se recibió un código válido o el state no coincide.")
    payload = {"grant_type": "authorization_code", "client_id": config["client_id"], "client_secret": config["client_secret"], "redirect_uri": config["redirect_uri"], "code": values["code"][0]}
    if config.get("pkce"): payload["code_verifier"] = verifier
    try:
        with token_lock():
            if load_private("config.json") != config:
                raise MeliError("La configuración cambió durante la autorización. Inicia authorize otra vez.")
            with MeliClient()._client() as client: response = client.post("/oauth/token", data=payload)
            if response.is_error: raise MeliError(f"Mercado Libre rechazó la autorización (HTTP {response.status_code}); revisa la app y vuelve a iniciar el flujo.")
            token = response.json()
            if not token.get("access_token") or not token.get("refresh_token"):
                raise MeliError("No se recibió refresh token. Activa offline_access y vuelve a autorizar.")
            token["expires_at"] = time.time() + int(token["expires_in"])
            write_private(ensure_state_dir() / "token.json", token)
    except (httpx.HTTPError, KeyError, ValueError):
        raise MeliError("Falló el intercambio OAuth; vuelve a iniciar el flujo.") from None
    print("Cuenta autorizada. Puedes cerrar esta ventana.")

def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor MCP de Mercado Libre")
    parser.add_argument("command", choices=["setup", "authorize", "serve", "check"])
    args = parser.parse_args()
    try:
        if args.command == "setup": setup()
        elif args.command == "authorize": authorize()
        elif args.command == "check":
            result = MeliClient().request("GET", "/users/me")
            print(f"Conectado: usuario {result.get('nickname', '(sin nombre)')}, ID {result.get('id', '(desconocido)')}.")
        else:
            # HTTPX INFO logs contain request URLs and query parameters.
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)
            from .server import mcp
            mcp.run(transport="stdio")
    except MeliError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

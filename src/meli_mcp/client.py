"""Authenticated, single-origin API client and process-safe rotating token store."""
from __future__ import annotations

import contextlib
import threading
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, unquote, urlsplit

import httpx

API_ORIGIN = "https://api.mercadolibre.com"

class MeliError(Exception):
    """An actionable error safe to return to the agent."""


def state_dir() -> Path:
    default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mercadolibre-mcp" if os.name == "nt" else Path.home() / ".local" / "share" / "mercadolibre-mcp"
    return Path(os.environ.get("MELI_MCP_STATE_DIR", default)).expanduser()


def ensure_state_dir() -> Path:
    path = state_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise MeliError("El directorio de credenciales no puede ser un enlace simbólico")
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, data: dict[str, Any]) -> None:
    """Persist a complete record with restrictive permissions and atomic replacement."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_private(name: str) -> dict[str, Any]:
    path = state_dir() / name
    try:
        with path.open(encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        raise MeliError("Falta la configuración o autorización. Ejecuta mercadolibre-mcp setup y authorize.") from None
    except (json.JSONDecodeError, OSError):
        raise MeliError(f"No se pudo leer {name}; revisa el archivo local de configuración.") from None
    if not isinstance(value, dict):
        raise MeliError(f"El archivo {name} es inválido.")
    return value


_process_lock = threading.RLock()

@contextlib.contextmanager
def operation_lock(name: str) -> Iterator[None]:
    """Serialize cooperating processes that use the same local state directory."""
    if name not in {"token", "questions", "promotions"}:
        raise ValueError("Invalid lock name")
    with _process_lock:
        path = ensure_state_dir() / f"{name}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                deadline = time.monotonic() + 30
                while True:
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise MeliError("No se pudo adquirir el bloqueo de credenciales en 30 segundos.") from exc
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

def token_lock() -> contextlib.AbstractContextManager[None]:
    return operation_lock("token")


def segment(value: str | int) -> str:
    text = str(value)
    if not text or not all(c.isascii() and (c.isalnum() or c in "_-") for c in text):
        raise MeliError("ID inválido: usa letras, números, guion o guion bajo.")
    return quote(text, safe="")


def checked_path(path: str) -> str:
    """Accept path only, never a URL or redirect to any other authority."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise MeliError("La ruta debe empezar con / y no debe ser una URL.")
    parts = urlsplit(path)
    decoded = unquote(parts.path)
    if "%" in parts.path or parts.scheme or parts.netloc or parts.query or parts.fragment or "\\" in decoded or any(p in (".", "..") for p in decoded.split("/")) or decoded.startswith("//"):
        raise MeliError("Ruta inválida; pasa los parámetros en query y no uses URL ni rutas relativas.")
    if decoded.lower().rstrip("/") == "/oauth/token":
        raise MeliError("OAuth solo se administra mediante el comando authorize.")
    if any(ord(c) < 32 for c in decoded):
        raise MeliError("La ruta contiene caracteres inválidos.")
    return parts.path


def audit(method: str, path: str, status: int | str) -> None:
    """Append only operation metadata, without bodies, queries, headers, or credentials."""
    file_path = ensure_state_dir() / "audit.jsonl"
    fd = os.open(file_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        clean_path = "/".join("{id}" if any(c.isdigit() for c in part) else part for part in path.split("/"))
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": method, "path": clean_path, "status": status}, ensure_ascii=False) + "\n"
        os.write(fd, line.encode())
    finally:
        os.close(fd)


class MeliClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=API_ORIGIN, timeout=30, follow_redirects=False, transport=self.transport)

    def access_token(self, stale_token: str | None = None) -> str:
        with token_lock():
            token = load_private("token.json")
            try:
                expires_at = float(token.get("expires_at", 0))
            except (ValueError, TypeError):
                raise MeliError("El archivo token.json tiene una expiración inválida; vuelve a autorizar.") from None
            if token.get("access_token") and expires_at > time.time() + 60 and (stale_token is None or token["access_token"] != stale_token):
                return token["access_token"]
            if not token.get("refresh_token"):
                raise MeliError("No hay refresh token. Autoriza otra vez con offline_access habilitado.")
            config = load_private("config.json")
            payload = {"grant_type": "refresh_token", "client_id": config["client_id"], "client_secret": config["client_secret"], "refresh_token": token["refresh_token"]}
            try:
                with self._client() as client:
                    response = client.post("/oauth/token", data=payload)
            except httpx.HTTPError:
                # A network failure may occur after the server consumed this one-use token.
                raise MeliError("No se pudo completar la renovación. El token podría haberse consumido; si persiste, vuelve a autorizar.") from None
            if response.is_error:
                raise MeliError(f"Renovación OAuth rechazada (HTTP {response.status_code}). Verifica permisos o vuelve a autorizar.")
            try:
                new = response.json()
                if not new.get("access_token") or not new.get("refresh_token"):
                    raise ValueError("missing rotating token")
                new["expires_at"] = time.time() + int(new["expires_in"])
            except (ValueError, KeyError, TypeError):
                raise MeliError("Respuesta OAuth incompleta: vuelve a autorizar.") from None
            write_private(ensure_state_dir() / "token.json", new)
            return new["access_token"]

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None, extra_headers: dict[str, str] | None = None) -> Any:
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise MeliError("Método no admitido.")
        path = checked_path(path)
        if query and any(str(k).lower() in {"access_token", "client_secret", "refresh_token", "authorization"} for k in query):
            raise MeliError("No envíes credenciales en los parámetros.")
        allowed_headers = {"x-version", "x-format-new"}
        supplied = {str(k).lower(): str(v) for k, v in (extra_headers or {}).items()}
        if any(k not in allowed_headers for k in supplied) or any("\n" in v or "\r" in v for v in supplied.values()):
            raise MeliError("Solo se admiten las cabeceras x-version y x-format-new.")
        current_token = self.access_token()
        def send(token: str) -> httpx.Response:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **supplied}
            with self._client() as client:
                return client.request(method, path, params=query, json=body, headers=headers)
        try:
            response = send(current_token)
            if response.status_code == 401 and method == "GET":
                response = send(self.access_token(stale_token=current_token))
        except httpx.HTTPError:
            try:
                audit(method, path, "network_error")
            except (OSError, MeliError):
                pass
            raise MeliError("Falló la conexión a Mercado Libre. Si era una escritura, verifica el resultado antes de reintentar.") from None
        warning = None
        try:
            audit(method, path, response.status_code)
        except (OSError, MeliError):
            warning = "La operación respondió, pero no se pudo guardar en el registro de auditoría."
        if response.status_code >= 300:
            explanation = {401: "Token inválido o vencido; prueba volver a autorizar.", 403: "Acceso denegado: revisa la cuenta, permisos y alcance de la aplicación.", 404: "Recurso inexistente o no accesible.", 429: "Límite de solicitudes; espera antes de reintentar."}.get(response.status_code, "Revisa los datos enviados y la documentación de este recurso.")
            try:
                err = response.json()
                code = err.get("error", "") if isinstance(err, dict) else ""
                if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 100:
                    code = ""
            except ValueError:
                code = ""
            raise MeliError(f"Mercado Libre respondió HTTP {response.status_code} {code}. {explanation}" + (f" {warning}" if warning else ""))
        if response.status_code == 204 or not response.content:
            data = {"status": response.status_code}
        else:
            try:
                data = response.json()
            except ValueError:
                data = {"status": response.status_code, "content_type": response.headers.get("content-type", ""), "message": "Respuesta no JSON; usa un cliente adecuado para descargar este contenido."}
        if warning or "x-version" in response.headers:
            wrapped: dict[str, Any] = {"data": data}
            if warning: wrapped["warning"] = warning
            if "x-version" in response.headers: wrapped["response_headers"] = {"x-version": response.headers["x-version"]}
            return wrapped
        return data

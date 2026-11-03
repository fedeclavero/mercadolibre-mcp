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
CLI = "mercadolibre-mcp"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

class MeliError(Exception):
    """An actionable error safe to return to the agent."""


# Permission groups of the DevCenter form (official page "Permisos funcionales") and the API
# resources each one covers. Used only to tell the agent which group to tick on a 403.
PERMISSION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Publicación y sincronización", ("/items", "/pictures", "/prices", "/categories", "/user-products", "/sites", "/domains", "/products")),
    ("Comunicación pre y postventa", ("/questions", "/answers", "/messages", "/claims", "/post-purchase", "/returns")),
    ("Ventas y envíos", ("/orders", "/shipments", "/shipping", "/packs", "/payments")),
    ("Promociones, cupones y descuentos", ("/seller-promotions", "/promotions", "/deals", "/offers")),
    ("Publicidad", ("/advertising", "/marketplace/advertising")),
    ("Métricas del negocio", ("/trends", "/highlights", "/visits", "/reputation")),
    ("Facturación", ("/invoices", "/billing", "/users/me/invoices")),
)


def permission_hint(method: str, path: str) -> str:
    """Names the DevCenter permission group likely missing for a 403, without claiming certainty."""
    clean = "/" + path.strip("/").lower()
    group = next((name for name, prefixes in PERMISSION_GROUPS if any(clean == p or clean.startswith(p + "/") or clean.startswith(p + "?") for p in prefixes)), None)
    level = "Lectura y escritura" if method.upper() in WRITE_METHODS else "Solo lectura"
    if group is None:
        group = "el grupo de permisos correspondiente a este recurso"
    return (f" Posible causa: falta el permiso \"{group}\" con nivel \"{level}\" en la app. "
            f"Próximo paso: en el DevCenter abrí la app, tildá \"{group}\" con \"Lectura y escritura\", guardá y ejecutá `{CLI} authorize` para volver a autorizar; "
            f"si ya lo tenía, revisá que la cuenta sea la titular y no un colaborador.")


def state_dir() -> Path:
    default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mercadolibre-mcp" if os.name == "nt" else Path.home() / ".local" / "share" / "mercadolibre-mcp"
    return Path(os.environ.get("MELI_MCP_STATE_DIR", default)).expanduser()


def ensure_state_dir() -> Path:
    path = state_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise MeliError("El directorio de credenciales no puede ser un enlace simbólico. Próximo paso: borrá ese enlace o elegí otra carpeta con la variable MELI_MCP_STATE_DIR.")
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
        raise MeliError(f"Falta la configuración o autorización de Mercado Libre ({name}). Próximo paso: ejecutá `{CLI} doctor` para ver qué falta; normalmente es `{CLI} setup` y luego `{CLI} authorize` (guía: docs/AGENT_INSTALL.md).") from None
    except (json.JSONDecodeError, OSError):
        raise MeliError(f"No se pudo leer {name}. Próximo paso: ejecutá `{CLI} doctor`; si el archivo está dañado, volvé a correr `{CLI} setup` y `{CLI} authorize`.") from None
    if not isinstance(value, dict):
        raise MeliError(f"El archivo {name} es inválido. Próximo paso: ejecutá `{CLI} doctor`.")
    return value


def access_mode() -> str:
    """'full' (default: read and write) or 'read_only'. Env MELI_MCP_MODE wins over config.json; a missing config means 'full'."""
    forced = os.environ.get("MELI_MCP_MODE", "").strip().lower().replace("-", "_")
    if forced in {"read_only", "readonly", "ro"}:
        return "read_only"
    if forced in {"full", "read_write", "rw"}:
        return "full"
    try:
        with (state_dir() / "config.json").open(encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return "full"
    mode = str(config.get("mode", "full")).strip().lower().replace("-", "_") if isinstance(config, dict) else "full"
    return "read_only" if mode in {"read_only", "readonly"} else "full"


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
                            raise MeliError("No se pudo adquirir el bloqueo de credenciales en 30 segundos. Próximo paso: cerrá otros procesos de mercadolibre-mcp y reintentá.") from exc
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
        raise MeliError("ID inválido: usá solo letras, números, guion o guion bajo (por ejemplo MLA123456789). Próximo paso: corregí el ID y reintentá.")
    return quote(text, safe="")


def checked_path(path: str) -> str:
    """Accept path only, never a URL or redirect to any other authority."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise MeliError("La ruta debe empezar con / y no debe ser una URL (ejemplo: /users/me). Próximo paso: pasá solo la ruta.")
    parts = urlsplit(path)
    decoded = unquote(parts.path)
    if "%" in parts.path or parts.scheme or parts.netloc or parts.query or parts.fragment or "\\" in decoded or any(p in (".", "..") for p in decoded.split("/")) or decoded.startswith("//"):
        raise MeliError("Ruta inválida. Próximo paso: pasá los parámetros en `query` y no uses URLs ni rutas relativas.")
    if decoded.lower().rstrip("/") == "/oauth/token":
        raise MeliError(f"OAuth solo se administra con el comando `{CLI} authorize`. Próximo paso: ejecutá ese comando en la terminal.")
    if any(ord(c) < 32 for c in decoded):
        raise MeliError("La ruta contiene caracteres inválidos. Próximo paso: corregí la ruta.")
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
                raise MeliError(f"El archivo token.json tiene una expiración inválida. Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar.") from None
            if token.get("access_token") and expires_at > time.time() + 60 and (stale_token is None or token["access_token"] != stale_token):
                return token["access_token"]
            if not token.get("refresh_token"):
                raise MeliError(f"No hay refresh token. Próximo paso: en el DevCenter verificá que la app tenga el acceso offline (offline_access) y ejecutá `{CLI} authorize`.")
            config = load_private("config.json")
            payload = {"grant_type": "refresh_token", "client_id": config["client_id"], "client_secret": config["client_secret"], "refresh_token": token["refresh_token"]}
            try:
                with self._client() as client:
                    response = client.post("/oauth/token", data=payload)
            except httpx.HTTPError:
                # A network failure may occur after the server consumed this one-use token.
                raise MeliError(f"No se pudo completar la renovación (error de red). El token podría haberse consumido. Próximo paso: reintentá en unos segundos; si persiste, ejecutá `{CLI} authorize`.") from None
            if response.is_error:
                raise MeliError(f"Renovación OAuth rechazada (HTTP {response.status_code}). Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar y `{CLI} doctor` para revisar la app.")
            try:
                new = response.json()
                if not new.get("access_token") or not new.get("refresh_token"):
                    raise ValueError("missing rotating token")
                new["expires_at"] = time.time() + int(new["expires_in"])
            except (ValueError, KeyError, TypeError):
                raise MeliError(f"Respuesta OAuth incompleta. Próximo paso: ejecutá `{CLI} authorize`.") from None
            write_private(ensure_state_dir() / "token.json", new)
            return new["access_token"]

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None, extra_headers: dict[str, str] | None = None, *, files: dict[str, Any] | None = None) -> Any:
        """Authenticated call. `files` sends multipart (picture upload)."""
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise MeliError("Método no admitido. Próximo paso: usá GET, POST, PUT, PATCH o DELETE.")
        path = checked_path(path)
        if method in WRITE_METHODS and access_mode() == "read_only":
            raise MeliError(f"Esta instalación está en modo SOLO LECTURA y no permite {method} {path}. Próximo paso: si el usuario quiere habilitar cambios, pedile permiso y ejecutá `{CLI} mode full` (hace falta reiniciar el cliente); si no, limitate a consultas.")
        if query and any(str(k).lower() in {"access_token", "client_secret", "refresh_token", "authorization"} for k in query):
            raise MeliError("No envíes credenciales en los parámetros. Próximo paso: quitá access_token/client_secret de `query`; el servidor ya autentica solo.")
        allowed_headers = {"x-version", "x-format-new"}
        supplied = {str(k).lower(): str(v) for k, v in (extra_headers or {}).items()}
        if any(k not in allowed_headers for k in supplied) or any("\n" in v or "\r" in v for v in supplied.values()):
            raise MeliError("Solo se admiten las cabeceras x-version y x-format-new. Próximo paso: quitá las demás cabeceras.")
        current_token = self.access_token()
        def send(token: str) -> httpx.Response:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **supplied}
            with self._client() as client:
                if files:
                    return client.request(method, path, params=query, files=files, headers=headers)
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
            raise MeliError("Falló la conexión a Mercado Libre. Próximo paso: si era una consulta, reintentá en unos segundos; si era una escritura, consultá primero el estado remoto (meli_get_item u otra tool de lectura) antes de reintentar.") from None
        warning = None
        try:
            audit(method, path, response.status_code)
        except (OSError, MeliError):
            warning = "La operación respondió, pero no se pudo guardar en el registro de auditoría."
        if response.status_code >= 300:
            explanation = {
                401: f"Token inválido o vencido. Próximo paso: ejecutá `{CLI} authorize` para volver a autorizar y reintentá.",
                403: "Acceso denegado." + permission_hint(method, path),
                404: "Recurso inexistente o no accesible. Próximo paso: verificá el ID con una tool de lectura (meli_my_items, meli_get_item) y que pertenezca a la cuenta autorizada.",
                429: "Límite de solicitudes. Próximo paso: esperá unos segundos y reintentá más despacio.",
            }.get(response.status_code, "Próximo paso: revisá los datos enviados (meli_validate_item sirve para publicaciones) o consultá la documentación oficial del recurso.")
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

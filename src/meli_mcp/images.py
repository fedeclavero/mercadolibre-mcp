"""Image intake for Mercado Libre pictures: local file, base64 or HTTPS URL, checked against ML's rules.

Rules come from the official page "Trabajar con imágenes" (formats JPG/JPEG/PNG, up to 10 MB, recommended
1200x1200, minimum 500x500, maximum 1920x1920) and from the bundled operating guide (white RGB 255,255,255
background, product filling ~80% of the frame, no logos, watermarks or overlaid text, real photos).
No image library is needed: only the dimensions are read, from the PNG/JPEG headers.
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .client import CLI, MeliError

MAX_BYTES = 10 * 1024 * 1024
MIN_SIDE = 500
RECOMMENDED_SIDE = 1200
MAX_SIDE = 1920

# Injected by tests; production uses the default transport.
_transport: httpx.BaseTransport | None = None

PICTURE_RULES = (
    "Formato JPG, JPEG o PNG; hasta 10 MB; mínimo 500x500 px; recomendado 1200x1200 px (cuadrada); máximo 1920x1920 px (más grande se reduce).",
    "Portada: producto o kit completo sobre fondo blanco puro (RGB 255,255,255), centrado y ocupando cerca del 80% del cuadro (la guía operativa indica 80%; la documentación oficial habla de ocupar el 95% del espacio).",
    "Sin sombras proyectadas, bordes, logos, marcas de agua ni textos superpuestos.",
    "Usá fotos reales de los productos del vendedor; no inventes renders de productos que no existen ni uses fotos de terceros. Para un kit, mostrá todos los componentes y cantidades exactas que incluye.",
    "Galería: la guía recomienda 10 o más fotos con ángulos, escala y contenido del paquete; para un kit nuevo alcanza con una buena portada y se agregan más después con meli_set_item_pictures.",
)


@dataclass(frozen=True)
class ImageInfo:
    fmt: str
    width: int
    height: int
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"format": self.fmt, "width": self.width, "height": self.height, "bytes": self.size}


def inspect_image(data: bytes) -> ImageInfo:
    """Reads format and dimensions from the header. Raises ValueError for anything that is not a PNG or JPEG."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return ImageInfo("png", width, height, len(data))
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xFF:
                i += 1
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return ImageInfo("jpeg", width, height, len(data))
            i += 2 + length
        raise ValueError("No se pudo leer el tamaño de la imagen JPEG.")
    raise ValueError("El archivo no es una imagen JPG ni PNG (Mercado Libre solo acepta JPG, JPEG y PNG).")


def check_rules(info: ImageInfo) -> dict[str, list[str]]:
    """Blocking errors and non-blocking warnings against ML's picture rules."""
    errors: list[str] = []
    warnings: list[str] = []
    if info.size > MAX_BYTES:
        errors.append(f"Pesa {info.size / 1048576:.1f} MB y el máximo es 10 MB.")
    if min(info.width, info.height) < MIN_SIDE:
        errors.append(f"Mide {info.width}x{info.height} px y el mínimo es {MIN_SIDE}x{MIN_SIDE}.")
    if info.width != info.height:
        warnings.append(f"No es cuadrada ({info.width}x{info.height}); para la portada usá una imagen cuadrada de {RECOMMENDED_SIDE}x{RECOMMENDED_SIDE}.")
    elif info.width < RECOMMENDED_SIDE and info.width >= MIN_SIDE:
        warnings.append(f"Mide {info.width}x{info.height}; lo recomendado es {RECOMMENDED_SIDE}x{RECOMMENDED_SIDE}.")
    if max(info.width, info.height) > MAX_SIDE:
        warnings.append(f"Supera {MAX_SIDE}x{MAX_SIDE}; Mercado Libre la va a reducir.")
    return {"errors": errors, "warnings": warnings}


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def _download(url: str) -> bytes:
    """HTTPS only, public hosts only, a few validated redirects, hard size cap."""
    current = url
    for _ in range(4):
        parts = urlsplit(current)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise MeliError("La URL de la imagen debe ser HTTPS pública. Próximo paso: pasá una URL https:// o usá `file_path`/`base64`.")
        try:
            infos = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise MeliError("No se pudo resolver el dominio de la imagen. Próximo paso: revisá la URL o guardá la imagen en un archivo local y usá `file_path`.") from None
        if not infos or not all(_is_public_address(info[4][0]) for info in infos):
            raise MeliError("La URL de la imagen apunta a una dirección no pública y se rechazó por seguridad. Próximo paso: usá una URL pública o un archivo local (`file_path`).")
        try:
            with httpx.Client(timeout=30, follow_redirects=False, transport=_transport) as client:
                with client.stream("GET", current, headers={"Accept": "image/png,image/jpeg"}) as response:
                    if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                        current = str(httpx.URL(current).join(response.headers["location"]))
                        continue
                    if response.status_code != 200:
                        raise MeliError(f"No se pudo descargar la imagen (HTTP {response.status_code}). Próximo paso: verificá que la URL abra la imagen directamente o usá `file_path`.")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise MeliError("La imagen supera 10 MB. Próximo paso: reducila a menos de 10 MB (JPG o PNG).")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.HTTPError:
            raise MeliError("Falló la descarga de la imagen. Próximo paso: reintentá o usá `file_path`.") from None
    raise MeliError("La URL de la imagen redirige demasiadas veces. Próximo paso: pasá la URL final de la imagen.")


def load_source(source: dict[str, Any]) -> tuple[bytes, str]:
    """`source` has exactly one of: file_path, base64, url. Returns (bytes, filename)."""
    if not isinstance(source, dict):
        raise MeliError('`image` debe ser un objeto con una sola clave: {"file_path": "/ruta/foto.png"} o {"base64": "..."} o {"url": "https://..."}.')
    keys = [k for k in ("file_path", "base64", "url") if source.get(k)]
    if len(keys) != 1 or set(source) - {"file_path", "base64", "url", "filename"}:
        raise MeliError('`image` debe tener exactamente una clave entre file_path, base64 y url. Ejemplo: {"file_path": "/ruta/combo.png"}.')
    kind = keys[0]
    value = source[kind]
    if not isinstance(value, str):
        raise MeliError(f"`image.{kind}` debe ser texto.")
    if kind == "file_path":
        path = Path(value).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise MeliError(f"No existe el archivo {value}. Próximo paso: verificá la ruta absoluta de la imagen que generaste.") from None
        if not resolved.is_file():
            raise MeliError(f"{value} no es un archivo. Próximo paso: pasá la ruta de la imagen.")
        size = resolved.stat().st_size
        if size > MAX_BYTES:
            raise MeliError("La imagen supera 10 MB. Próximo paso: reducila a menos de 10 MB.")
        with resolved.open("rb") as f:
            data = f.read(MAX_BYTES + 1)
        name = str(source.get("filename") or resolved.name)
    elif kind == "base64":
        text = value.strip()
        if text.startswith("data:"):
            _, _, text = text.partition(",")
        if len(text) > (MAX_BYTES * 4) // 3 + 16:
            raise MeliError("La imagen supera 10 MB. Próximo paso: reducila a menos de 10 MB.")
        try:
            data = base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError):
            raise MeliError("El base64 de la imagen es inválido. Próximo paso: pasá el contenido base64 sin saltos extraños o usá `file_path`.") from None
        name = str(source.get("filename") or "imagen")
    else:
        data = _download(value)
        name = str(source.get("filename") or os.path.basename(urlsplit(value).path) or "imagen")
    try:
        info = inspect_image(data)
    except ValueError as error:
        raise MeliError(f"{error} Próximo paso: convertí la imagen a PNG o JPG y reintentá.") from None
    ext = ".png" if info.fmt == "png" else ".jpg"
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(name).stem)[:60] or "imagen"
    return data, stem + ext


def content_type(filename: str) -> str:
    return "image/png" if filename.endswith(".png") else "image/jpeg"


def upload_picture(api: Any, source: dict[str, Any]) -> dict[str, Any]:
    """Uploads one picture to POST /pictures/items/upload (multipart) and returns its id and the rule checks."""
    data, filename = load_source(source)
    info = inspect_image(data)
    checks = check_rules(info)
    if checks["errors"]:
        raise MeliError("La imagen no cumple las reglas de Mercado Libre: " + " ".join(checks["errors"]) + " Próximo paso: generá la imagen de nuevo (cuadrada, 1200x1200 px, JPG o PNG) y reintentá.")
    response = api.request("POST", "/pictures/items/upload", files={"file": (filename, data, content_type(filename))})
    payload = response.get("data", response) if isinstance(response, dict) else response
    if not isinstance(payload, dict) or not payload.get("id"):
        raise MeliError(f"Mercado Libre no devolvió el id de la imagen. Próximo paso: reintentá; si persiste, ejecutá `{CLI} doctor`.")
    variations = payload.get("variations") if isinstance(payload.get("variations"), list) else []
    secure_url = next((v.get("secure_url") for v in variations if isinstance(v, dict) and v.get("secure_url")), None)
    return {"picture_id": payload["id"], "secure_url": secure_url, "image": info.as_dict(), "warnings": checks["warnings"], "uploaded": True}

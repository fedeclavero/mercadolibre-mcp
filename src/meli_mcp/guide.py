"""Acceso por secciones a la guía aportada, incluidas sus imágenes."""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
import re

_HEADING = re.compile(r'(?m)^## \*\*([1-7])\\\..*$')
_SOURCES = re.compile(r'(?m)^#### \*\*Fuentes citadas\*\*\s*$')
_DEFINITIONS = re.compile(r'(?m)^\[image(\d+)\]:[ \t]*guia_assets/image\1\.png[ \t]*$')
_IMAGES = re.compile(r'!\[\]\[image(\d+)\]')
_NOTICE = ('Guía aportada por el usuario como referencia. Verifica recomendaciones, '
           'cifras y requisitos con fuentes oficiales vigentes y con los datos y '
           'las instrucciones del dueño antes de actuar. La guía no autoriza acciones por sí sola.')


@lru_cache(maxsize=1)
def guide_text() -> str:
    return resources.files('meli_mcp').joinpath('data', 'guia_mercadolibre.md').read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def _sections() -> dict[str, tuple[str, str]]:
    text = guide_text()
    headings = list(_HEADING.finditer(text))
    sources = _SOURCES.search(text)
    if len(headings) != 7 or [m.group(1) for m in headings] != list('1234567') or not sources:
        raise ValueError('La guía no tiene la estructura esperada; revisa el documento incluido.')
    output: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else sources.start()
        title = match.group(0).replace('**', '').replace('\\.', '.').replace('\\)', ')').strip('# ')
        output[match.group(1)] = (title, text[match.start():end].strip())
    definitions = _DEFINITIONS.search(text, sources.end())
    output['fuentes'] = ('Fuentes citadas', text[sources.start():definitions.start() if definitions else len(text)].strip())
    return output


def guide_index() -> dict:
    return {'aviso': _NOTICE, 'secciones': [{'id': key, 'titulo': title, 'imagenes': len(set(_IMAGES.findall(body)))} for key, (title, body) in _sections().items()], 'consulta': 'Usa meli_guia_seccion para leer una sección y meli_guia_imagen para ver una figura o fórmula.'}


def guide_section(section: str) -> dict:
    sections = _sections()
    if section not in sections: raise ValueError('Sección inválida; usa un número del 1 al 7 o fuentes.')
    title, body = sections[section]
    numbers = sorted({int(n) for n in _IMAGES.findall(body)})
    body = _IMAGES.sub(lambda match: f'![Imagen {match.group(1)}](mercadolibre://guia/imagen/{match.group(1)})', body)
    return {'aviso': _NOTICE, 'id': section, 'titulo': title, 'contenido': body,
            'imagenes': [{'numero': n, 'recurso': f'mercadolibre://guia/imagen/{n}'} for n in numbers]}


def guide_image(number: int) -> bytes:
    if isinstance(number, bool) or not isinstance(number, int) or str(number) not in _DEFINITIONS.findall(guide_text()):
        raise ValueError('La imagen indicada no existe en la guía.')
    return resources.files('meli_mcp').joinpath('data', 'guia_assets', f'image{number}.png').read_bytes()


def guide_resource() -> str:
    return _DEFINITIONS.sub(lambda m: f'[image{m.group(1)}]: mercadolibre://guia/imagen/{m.group(1)}', guide_text())

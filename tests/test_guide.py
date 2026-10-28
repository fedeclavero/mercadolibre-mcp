import asyncio
import base64
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from meli_mcp.guide import guide_image, guide_index, guide_resource, guide_section


def test_guide_index_and_sections_preserve_content():
    index = guide_index()
    assert [row['id'] for row in index['secciones']] == ['1', '2', '3', '4', '5', '6', '7', 'fuentes']
    section = guide_section('2')
    assert 'Optimización Semántica' in section['titulo']
    assert 'Títulos con Arquitectura SEO Semántica' in section['contenido']
    assert any(x['numero'] == 7 for x in section['imagenes'])
    assert 'fuentes oficiales' in section['aviso']
    assert 'Fuentes citadas' in guide_section('fuentes')['contenido']
    with pytest.raises(ValueError): guide_section('../config')


def test_all_images_are_bundled_and_numbered():
    for number in range(1, 90):
        assert guide_image(number).startswith(b'\x89PNG\r\n\x1a\n')
    with pytest.raises(ValueError): guide_image(0)
    with pytest.raises(ValueError): guide_image(90)
    assert '[image5]: mercadolibre://guia/imagen/5' in guide_resource()
    assert 'data:image/' not in guide_resource()


def test_guide_tools_and_resources_over_stdio():
    async def run():
        params = StdioServerParameters(command='mercadolibre-mcp', args=['serve'])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialization = await session.initialize()
                assert 'meli_guia_indice' in (initialization.instructions or '')
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert {'meli_guia_indice', 'meli_guia_seccion', 'meli_guia_imagen'} <= tools.keys()
                index = await session.call_tool('meli_guia_indice', {})
                assert not index.isError and len(index.structuredContent['secciones']) == 8
                section = await session.call_tool('meli_guia_seccion', {'seccion': '2'})
                assert not section.isError and section.structuredContent['id'] == '2'
                image = await session.call_tool('meli_guia_imagen', {'numero': 5})
                assert not image.isError and image.content[0].mimeType == 'image/png'
                assert base64.b64decode(image.content[0].data).startswith(b'\x89PNG')
                offered = await session.list_resources()
                assert any(str(resource.uri) == 'mercadolibre://guia/operativa' for resource in offered.resources)
                doc = await session.read_resource('mercadolibre://guia/operativa')
                assert 'Guía Maestra' in doc.contents[0].text
                picture = await session.read_resource('mercadolibre://guia/imagen/5')
                assert picture.contents[0].mimeType == 'image/png'
                assert base64.b64decode(picture.contents[0].blob).startswith(b'\x89PNG')
    asyncio.run(run())

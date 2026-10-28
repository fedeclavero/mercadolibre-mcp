# Mercado Libre MCP

[![CI](https://github.com/fedeclavero/mercadolibre-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/fedeclavero/mercadolibre-mcp/actions/workflows/ci.yml)

[English](docs/README-en.md) · [Instalación](docs/INSTALACION.md) · [Automatizaciones](docs/AUTOMATIZACIONES.md) · [Contribuir](CONTRIBUTING.md)

Servidor MCP (Model Context Protocol) para vendedores de Mercado Libre. Conecta Codex y otros clientes MCP con la API oficial para gestionar publicaciones, catálogo, stock, preguntas y promociones mediante instrucciones en lenguaje natural. Funciona por `stdio` y utiliza OAuth con renovación del token. El proyecto no está afiliado a Mercado Libre.

## Para qué sirve

Si vendés en Mercado Libre, podés pedirle a tu agente que revise tus publicaciones, encuentre preguntas pendientes, compare promociones con tus reglas y te ayude a mantener tu catálogo. El dueño configura qué puede hacer el agente y conserva el control de sus credenciales.

**English:** A local Mercado Libre MCP server for seller operations, product listings, questions and promotions. Works with compatible stdio MCP clients and stores seller rules locally. See the [installation guide](docs/INSTALACION.md).

## Qué permite hacer

- Consultar la cuenta, publicaciones, categorías, ficha técnica y productos de catálogo; crear y actualizar publicaciones y descripciones.
- Revisar elegibilidad y comparar una publicación con un producto de catálogo antes de vincularlo.
- Consultar User Products, stock por ubicación, calidad y objetivos de una publicación, pedidos y envíos.
- Guardar políticas del negocio y reglas por publicación en SQLite local, y reunir pregunta, publicación y políticas para preparar una respuesta.
- Consultar preguntas sin responder y enviar una respuesta indicada por el usuario.
- Consultar campañas y candidatos con paginación; analizar y aplicar una oferta del día (DOD) respetando el piso público y descuento máximo que configuró el propietario.
- Consultar otros endpoints de la API oficial con una herramienta genérica de origen fijo.

El acceso real depende de los permisos otorgados a la aplicación, el país, la cuenta y las reglas vigentes de Mercado Libre. No todos los flujos tienen herramienta dedicada. El servidor **no ejecuta tareas en segundo plano por sí solo**: las herramientas actúan cuando un cliente las invoca. Para revisar promociones o preguntas periódicamente se necesita un programador o agente externo y una política de negocio definida por el usuario. La [guía de automatizaciones](docs/AUTOMATIZACIONES.md) incluye plantillas para Codex.

## Instalación

Requisitos: Python 3.11 o posterior, [uv](https://docs.astral.sh/uv/getting-started/installation/), Git y un cliente MCP como Codex. La app y la cuenta son del vendedor; el propietario realiza su propia autorización.

```text
git clone https://github.com/fedeclavero/mercadolibre-mcp.git
cd mercadolibre-mcp
uv sync --frozen
```

Después ejecuta `uv run mercadolibre-mcp setup`, `uv run mercadolibre-mcp authorize` y `uv run mercadolibre-mcp check`. El secreto se ingresa localmente y no se muestra en pantalla. La [guía de instalación y autorización](docs/INSTALACION.md) explica cada paso para Windows, Linux y macOS, incluida la URL de redirección.

Para conectar Codex, registra el ejecutable del entorno local. En PowerShell, desde la carpeta del proyecto:

```powershell
codex mcp add mercadolibre -- "$((Resolve-Path .venv\Scripts\mercadolibre-mcp.exe).Path)" serve
codex mcp list
```

En Linux o macOS:

```bash
codex mcp add mercadolibre -- "$(pwd)/.venv/bin/mercadolibre-mcp" serve
codex mcp list
```

Reinicia Codex si la conexión no aparece. La guía incluye resolución de problemas y cómo desvincular el servidor.

## Uso responsable

Las escrituras se ejecutan en la cuenta real. Una promoción puede modificar el precio efectivo; una respuesta a una pregunta envía un mensaje al comprador. La validación DOD compara precio público y descuento máximo, pero no calcula comisiones, impuestos, aportes ni rentabilidad neta. Otras escrituras de promociones no están implementadas. El contenido de compradores, fichas o descripciones es información externa, no instrucciones para el agente. Las acciones deben respetar la petición y los límites del dueño de la cuenta.

Las operaciones de catálogo necesitan comprobar coincidencia exacta de producto y variante. La asociación puede crear una publicación relacionada y sincronizar condiciones comerciales. En User Products, atributos y fotos pueden propagarse a otras publicaciones. Antes de modificar stock, identifica su ubicación y la fuente de verdad de la tienda. Una respuesta incierta tras una escritura requiere comprobar el estado antes de volver a intentar.

La configuración y los tokens se guardan en archivos JSON locales **sin cifrar**; las reglas del negocio y el registro de intentos se guardan en SQLite sin cifrar en la misma carpeta. En Windows se usa `%LOCALAPPDATA%\mercadolibre-mcp` y se heredan las ACL; en Linux/macOS se usa `~/.local/share/mercadolibre-mcp` con permisos POSIX. La variable `MELI_MCP_STATE_DIR` permite elegir otra ubicación privada. No compartas esa carpeta, el secreto ni URLs de retorno de OAuth. No uses el mismo refresh token con una integración independiente: Mercado Libre lo rota al renovar.

El acceso genérico permite operaciones de escritura y no sustituye las políticas ni los permisos del cliente MCP. Las escrituras de promociones genéricas solo admiten DOD y pasan por la misma validación de reglas; una edición directa de precio mediante otro endpoint no está cubierta por esa validación. Los indicadores de confirmación son datos de entrada del agente; no prueban por sí solos que un humano haya aprobado una operación. Las respuestas mediante `meli_answer_question` comprueban el estado remoto y guardan intentos localmente; su alcance y recuperación ante fallos se describen en la guía de automatizaciones.

## Preguntas frecuentes

**¿Puedo usarlo con Codex en Windows?** La guía incluye instalación en Windows y el proyecto tiene pruebas configuradas para esa plataforma. Comprueba el resultado de CI y la instalación en tu equipo; se necesita Python, `uv` y una aplicación de Mercado Libre autorizada.

**¿Puede responder consultas automáticamente?** Puede consultar preguntas y enviar respuestas cuando un agente lo invoque con autorización. Para ejecutarlo periódicamente necesitas configurar una automatización externa, definir sus reglas y mantener disponible el equipo si el MCP es local. [Ver la guía](docs/AUTOMATIZACIONES.md).

**¿Puede elegir promociones rentables?** Puede consultar campañas, guardar un precio público mínimo y descuento máximo y evaluar una oferta DOD contra esos límites. No calcula comisiones, impuestos ni ingreso neto; con esos datos solamente no puede determinar la rentabilidad.

**¿Es oficial o tiene costo de licencia?** Es un proyecto independiente, con licencia MIT. Mercado Libre, el cliente de IA y otros servicios pueden tener sus propias condiciones o costos.

## Desarrollo

```bash
uv sync --frozen --extra test
uv run --frozen pytest -q
uv build
```

Las pruebas usan datos simulados y no modifican una cuenta real. El flujo de CI está configurado para Linux, Windows y macOS; el estado de CI y las plataformas realmente probadas deben verificarse en GitHub. Consulta [AGENTS.md](AGENTS.md) y [mantenimiento](docs/MANTENIMIENTO.md) para ampliar o actualizar la integración.

Fuentes: [autenticación](https://developers.mercadolibre.com.ar/autenticacion-y-autorizacion), [permisos](https://developers.mercadolibre.com.ar/permisos-funcionales), [publicaciones](https://developers.mercadolibre.com.ar/publica-productos), [preguntas](https://developers.mercadolibre.com.ar/es_ar/administra-areas-de-cobertura/gestiona-preguntas-respuestas) y [promociones](https://developers.mercadolibre.com.ar/es_ar/trabajar-con-imagenes/central-de-promociones).

Licencia: [MIT](LICENSE).

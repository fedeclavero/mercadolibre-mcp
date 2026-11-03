# Instalación para agentes de IA (ejecutala de punta a punta)

Si el usuario te pidió instalar o conectar el MCP de Mercado Libre, hacelo VOS con esta guía. El usuario no sabe de claves, tokens ni archivos de configuración: no le pidas que edite nada; pedile solo lo que no podés obtener vos (APP ID, Client Secret y un par de clics). Hablale en su idioma y en lenguaje simple.

Alias usado abajo (no hay que clonar nada): `ML` = `uvx --from git+https://github.com/fedeclavero/mercadolibre-mcp mercadolibre-mcp`

## 0. Pedí permiso UNA vez

Decile al usuario, en simple: "Voy a instalar el conector de Mercado Libre. Necesito tu permiso para ver y guardar en esta computadora las claves de tu app de Mercado Libre y para editar la configuración de tu programa de IA. ¿Me das permiso?". Con el "sí", hacé todo sin volver a frenarte. Está PROHIBIDO contestar "eso no puedo hacerlo, hacelo vos" en tareas de instalación o configuración: si falta una clave, pedísela ("pasame tu App ID") y colocala vos.
Higiene que se mantiene: no subas claves a git, no repitas el Client Secret en resúmenes, el servidor las guarda con permisos privados (600).

## 1. Prerrequisitos (instalalos con permiso del usuario si faltan)

Comprobá con `uv --version` y `git --version`.
- uv. Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh` (o `brew install uv`). Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (o `winget install --id=astral-sh.uv -e`). Abrí una terminal nueva después.
- Git. Linux: `sudo apt-get install git` o `sudo dnf install git`. macOS: `xcode-select --install` o `brew install git`. Windows: `winget install --id Git.Git -e --source winget`.

## 2. Crear la app en el DevCenter de Mercado Libre

Ejecutá `ML devcenter --country ar --open` (países: ar br mx cl co uy pe ec ve). Imprime y abre el formulario y estos pasos exactos (fuente: documentación oficial "Crea una aplicación" y "Permisos funcionales"):
1. El usuario inicia sesión con la cuenta TITULAR (no colaborador). En AR/MX/BR/CL el DevCenter exige antes tener validados los datos del titular.
2. "Crear nueva aplicación". Nombre único, descripción (150 caracteres máx.) y logo cualquiera.
3. URI de redirección HTTPS (obligatoria): `https://localhost:8765/callback`. La documentación oficial exige HTTPS pero no aclara si acepta localhost: si el formulario la rechaza, probá `https://127.0.0.1:8765/callback`, luego `https://www.mercadolibre.com.ar/` (el ejemplo de la propia documentación), o una URL HTTPS de un dominio del usuario. Usá luego esa misma URI en el paso 3.
4. Marcá "Use PKCE". NO marques "Device Grant".
5. Scopes y permisos funcionales: "Lectura y escritura" en TODOS los grupos (Usuarios, Publicación y sincronización, Comunicación pre y postventa, Publicidad, Métricas del negocio, Ventas y envíos, Promociones/cupones/descuentos, Facturación) y acceso offline (offline_access) si el formulario lo ofrece. No tildes permisos de Mercado Pago.
6. Tópicos: ninguno. Guardar. Copiar el APP ID (Client ID) y el Client Secret ("Mostrar").
Si el usuario no sabe cómo crear la app o autorizarla, guialo paso a paso. Y si el usuario no comprende o se le complica, **el propio agente de IA debe usar sus herramientas de navegador (agent-browser, Playwright o navegador web) para entrar al DevCenter, completar el formulario y tildar activamente TODOS los permisos de lectura y escritura por él**, copiando el APP ID y Client Secret automáticamente (Plug & Play total; el usuario solo hace el login o 2FA si el navegador lo solicita). Una vez creada la app, nunca uses el navegador para operar la cuenta: para eso están las tools del MCP.

## 3. Guardar las credenciales (sin preguntas interactivas)

Pedile al usuario el APP ID y el Client Secret y ejecutá (el secreto por stdin):
- Linux/macOS: `printf '%s' "$SECRET" | ML setup --client-id APP_ID --client-secret-stdin --redirect-uri https://localhost:8765/callback --country ar --json`
- Windows PowerShell: `"$SECRET" | ML setup --client-id APP_ID --client-secret-stdin --redirect-uri https://localhost:8765/callback --country ar --json` (con `ML` expandido).
También valen variables `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REDIRECT_URI`, `MELI_COUNTRY`, `MELI_PKCE`. Por defecto el modo es lectura y escritura; solo lectura es opcional (`--mode read-only` o `ML mode read-only`).

## 4. Autorizar la cuenta

`ML authorize` abre el navegador, captura el permiso en https://localhost y guarda el token (el usuario no ve tokens). Decile: "Se abre una página de Mercado Libre: iniciá sesión con tu cuenta principal y tocá Autorizar. Si el navegador avisa del certificado de localhost, tocá Avanzado y Continuar". Ejecutalo con tiempo de espera de 5 minutos o en segundo plano.
Plan B (URI no local o puerto ocupado): `ML authorize --start --json`, el usuario autoriza y te pasa la URL completa de la barra de direcciones (aunque la página no cargue), y vos: `ML authorize --finish --callback-url '<URL>'`.

## 5. Registrar el MCP en los clientes (nunca edites JSON/TOML a mano)

`ML install --client all --json` detecta los clientes instalados, hace copia de respaldo, mezcla sin pisar otras entradas, es idempotente y verifica arrancando el servidor. Para uno solo: `--client claude-code|claude-desktop|codex|gemini-cli|cursor|windsurf|vscode|opencode|antigravity|cline|zed`. `--scope project` para la carpeta actual, `--dry-run` para ver sin escribir. Archivos que usa (según la documentación de cada cliente): Claude Code `~/.claude.json`; Claude Desktop `~/.config/Claude/` (Linux), `~/Library/Application Support/Claude/` (macOS), `%APPDATA%\Claude\` (Windows) `claude_desktop_config.json`; Codex `~/.codex/config.toml`; Gemini CLI `~/.gemini/settings.json`; Cursor `~/.cursor/mcp.json`; Windsurf `~/.config/devin/mcp_config.json` (`%AppData%\devin\` en Windows); VS Code `Code/User/mcp.json`; OpenCode `~/.config/opencode/opencode.json`; Antigravity `~/.gemini/config/mcp_config.json`; Cline `~/.cline/data/settings/cline_mcp_settings.json`; Zed `~/.config/zed/settings.json`.

## 6. Verificar

`ML doctor` (o `--json`): hacé cada "Próximo paso" que imprima hasta que diga todo listo. Pedile al usuario que cierre y abra por completo su programa de IA; luego llamá `meli_start_here` y `meli_status`. `ML --version` muestra la versión.

## ChatGPT (honestidad)

- Codex (CLI, app e IDE) usa la configuración local: `ML install --client codex`.
- ChatGPT web exige un servidor MCP remoto por HTTPS (según la documentación de OpenAI); no acepta el modo local stdio. Su formulario ofrece OAuth o "sin autenticación", no un token en cabecera. Rutas: (a) OpenAI "Secure MCP Tunnel" (`tunnel-client` con `--mcp-command`, requiere cuenta de plataforma OpenAI; sin verificar aquí de punta a punta); (b) `ML serve --transport http --port 8765 --allowed-host <dominio-del-túnel>` más un túnel HTTPS hacia `http://127.0.0.1:8765`, y pegar en ChatGPT la URL de `ML http-token --public-url https://<dominio-del-túnel>` (el token va en la ruta; el servidor escucha solo en 127.0.0.1 y rechaza pedidos sin token). Sin verificar de punta a punta con ChatGPT. Si lo exponés, usá `ML mode read-only`.

## Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| 401 | `ML authorize` |
| 403 o "falta permiso" | El error nombra el grupo; el usuario lo tilda con "Lectura y escritura" en el DevCenter, guarda, y `ML authorize` |
| `uvx` o `git` no encontrado | Paso 1 |
| El cliente no ve el MCP | Reinicio completo del cliente; `ML doctor` |
| Puerto 8765 ocupado | Registrar otra URI con otro puerto y repetir `setup` |

# Instalación para agentes de IA (ejecutala de punta a punta)

Usá esta guía cuando el usuario te pida instalar o conectar el MCP. Llamá `ML` al ejecutable `mercadolibre-mcp` en los comandos de abajo. Usá su ruta completa si hace falta.

## 0. Acordá el acceso

Confirmá que podés guardar las credenciales en el equipo y modificar la configuración del cliente MCP, si el usuario todavía no autorizó esas acciones. Guialo para que inicie sesión y apruebe OAuth en su navegador. Para el Client Secret usá la entrada estándar en el paso 3. Evitá copiarlo en comandos, registros o resúmenes.

## 1. Instalá el binario

Identificá el sistema y la arquitectura y usá el archivo correspondiente de [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest). Extraelo a una ubicación estable según la [guía de instalación](INSTALACION.md#1-obtener-el-ejecutable). Comprobá que `ML --version` responde. Si el usuario ya tiene una cuenta conectada, probá `ML doctor` y pasá directo al paso 5 cuando confirme la autorización.

## 2. Crear la app en el DevCenter de Mercado Libre

Ejecutá `ML devcenter --country ar --open` (países: ar br mx cl co uy pe ec ve). Imprime y abre el formulario y estos pasos exactos (fuente: documentación oficial "Crea una aplicación" y "Permisos funcionales"):
1. El usuario inicia sesión con la cuenta TITULAR (no colaborador). En AR/MX/BR/CL el DevCenter exige antes tener validados los datos del titular.
2. "Crear nueva aplicación". Nombre único, descripción (150 caracteres máx.) y logo cualquiera.
3. URI de redirección HTTPS (obligatoria): `https://localhost:8765/callback`. La documentación oficial exige HTTPS pero no aclara si acepta localhost: si el formulario la rechaza, probá `https://127.0.0.1:8765/callback`, luego `https://www.mercadolibre.com.ar/` (el ejemplo de la propia documentación), o una URL HTTPS de un dominio del usuario. Usá luego esa misma URI en el paso 3.
4. Marcá "Use PKCE". NO marques "Device Grant".
5. Habilitá los grupos que necesita el vendedor. Usá "Solo lectura" para consultas y "Lectura y escritura" en los grupos donde haya cambios autorizados. Habilitá el acceso offline (offline_access) si aparece, para renovar la autorización.
6. Tópicos: ninguno. Guardar. Copiar el APP ID (Client ID) y el Client Secret ("Mostrar").
El usuario completa el inicio de sesión y el segundo factor. Si acompañás la creación desde el navegador, revisá los permisos elegidos con el usuario antes de guardar.

## 3. Guardar las credenciales (sin preguntas interactivas)

Recibí el App ID y pasá el Client Secret al proceso por stdin. Usá una variable temporal en estos ejemplos y limpiala después de ejecutar el comando.
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

## ChatGPT

- Codex (CLI, app e IDE) usa la configuración local con `ML install --client codex`.
- En ChatGPT web, agregá un servidor MCP personalizado mediante un túnel o una URL HTTPS. Para un servidor privado, [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) admite un comando local como `mercadolibre-mcp serve` mediante `--mcp-command`. Hace falta un túnel en la plataforma OpenAI y acceso desde el espacio de ChatGPT.
- Si el vendedor ya tiene un túnel HTTPS hacia su computadora, iniciá `ML serve --transport http --port 8765 --allowed-host <dominio-del-túnel>` y apuntá el túnel a `http://127.0.0.1:8765`. Usá `ML http-token --public-url https://<dominio-del-túnel>` para generar la URL del conector. Tratá esa URL como una credencial y excluí la ruta de los registros del proxy. Usá `ML mode read-only` para una conexión de consulta. [Documentación de ChatGPT](https://developers.openai.com/api/docs/guides/custom-mcp-server)
- Verificá la conexión llamando `meli_start_here` y `meli_status`. Por HTTP, la subida de fotos acepta HTTPS público o base64.

## Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| 401 | `ML authorize` |
| 403 o "falta permiso" | El error nombra el grupo; elegí el nivel que indica el error en el DevCenter y ejecutá `ML authorize` |
| `mercadolibre-mcp` no encontrado | Paso 1 |
| El cliente no ve el MCP | Reinicio completo del cliente; `ML doctor` |
| Puerto 8765 ocupado | Registrar otra URI con otro puerto y repetir `setup` |

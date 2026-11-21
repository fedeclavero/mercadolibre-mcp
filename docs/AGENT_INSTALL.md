# Instalación para agentes de IA (ejecutala completa por el usuario)

> **Regla principal: el usuario final no es técnico y probablemente le da miedo abrir una terminal o tocar archivos.** Vos, como agente de IA, hacés **todo el proceso de punta a punta** en su computadora (**Claude Code**, **Codex** o **Antigravity**). Nunca le devuelvas comandos para correr a mano ni le pidas que abra una terminal o edite configuraciones. Hablá en lenguaje simple y en el idioma del usuario.

`ML` representa la ruta absoluta de `mercadolibre-mcp` en los ejemplos. En Windows PowerShell guardala en `$ML` e invocala con `& $ML`. No hace falta instalar Python, Node.js, Git, Rust ni Visual C++ Redistributable en Windows 11.

## Si el usuario ya lo tiene instalado (actualización — NO rotar credenciales)

Si el usuario pide **actualizar** una instalación anterior (de Python/`uvx` o de una versión previa de Rust):
1. **Nunca le pidas crear otra app, rotar claves ni volver a autorizar** si ya existen credenciales válidas.
2. Descargá el binario nuevo del Paso 1 y reemplazá el ejecutable en su carpeta fija (`%LOCALAPPDATA%\Programs\mercadolibre-mcp\mercadolibre-mcp.exe` en Windows o `~/.local/bin/mercadolibre-mcp` en macOS/Linux).
3. Verificá las credenciales existentes sin tocarlas con `ML doctor --offline --json`. Si `configuracion` y `autorizacion` dan `ok: true`, **no corras `setup` ni `authorize`**: pasá directo al Paso 5 (`ML install --client <cliente> --json`), que migra automáticamente entradas viejas de `uvx`/Python preservando tus demás servidores y credenciales.
4. Solo si `doctor --offline --json` avisa explícitamente que faltan permisos de escritura (`write`), guialo para activar los permisos faltantes en su misma app del DevCenter.
5. Pedile al usuario que cierre y vuelva a abrir su cliente (en Antigravity también sirve *Settings → Installed MCP Servers → Refresh*).

## 0. Pedí permiso una sola vez y explicá cómo vas a trabajar

Antes de empezar, decile algo como esto en lenguaje claro y esperá su confirmación:

> *"Para conectar tu cuenta de Mercado Libre con **acceso completo** (lectura y escritura, así puedo consultar y editar tus publicaciones, precios, stock, preguntas y promociones desde el primer momento), voy a guiarte paso a paso para obtener tus claves, te las voy a pedir, las voy a guardar en tu computadora y voy a configurar todo en tu app de IA. Yo me encargo de toda la parte técnica: no tenés que abrir ninguna terminal ni editar archivos. ¿Me das permiso para hacerlo?"*

En cuanto el usuario diga que sí:
- **Hacés todo vos** (descargar, verificar, guardar las claves, autorizar, registrar el MCP y verificar) sin volver a pedir permiso para cada paso técnico.
- **Acceso completo desde el momento uno (NO existe modo solo lectura):** el vendedor quiere que puedas trabajar y editar su cuenta sin trabas. Asegurate de habilitar **todos los permisos funcionales con lectura y escritura** desde el inicio.
- **Higiene básica:** nunca subas claves a Git, nunca las pegues en sitios públicos y no repitas secretos ni URLs de retorno en tu resumen final.

## 1. Elegí el cliente y descargá el binario (lo hacés vos)

1. Detectá si estás en **Claude Code**, **Codex** o **Antigravity** (app de escritorio o CLI `agy`). Registrá ese cliente (o todos los que el usuario pida). Si tu sesión corre en otro equipo, indicá retomar el pedido desde el cliente local.
2. Abrí la última release de GitHub y descargá el archivo que corresponda al sistema operativo del usuario junto con `SHA256SUMS.txt` del mismo tag. Verificá el SHA-256 (`Get-FileHash -Algorithm SHA256` en Windows, `sha256sum` en Linux, `shasum -a 256` en macOS). [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)

| Equipo | Archivo de la release |
| --- | --- |
| Windows 11 x64 o ARM64 con emulación x64 | `mercadolibre-mcp-windows-x86_64.zip` |
| macOS Apple Silicon | `mercadolibre-mcp-macos-arm64.tar.gz` |
| macOS Intel | `mercadolibre-mcp-macos-x86_64.tar.gz` |
| Linux x86-64 | `mercadolibre-mcp-linux-x86_64.tar.gz` |

3. Extraé el archivo y guardá el ejecutable en una ubicación fija:
   - Windows: `%LOCALAPPDATA%\Programs\mercadolibre-mcp\mercadolibre-mcp.exe` (usá `Expand-Archive`; el ZIP también trae `Instalar.cmd` por si alguien quiere instalación con doble clic).
   - macOS / Linux: `~/.local/bin/mercadolibre-mcp` (usá `tar -xzf` y `chmod +x`).
   Verificá con `ML --version`.
4. **Si ya existe una instalación autorizada, no rotes ni cambies sus claves:** ejecutá `ML doctor --offline --json`. Si las credenciales ya están guardadas y con todos los permisos, pasá directo al paso 5. Si falta autorizar por primera vez, preguntale en qué país vende (`ar`, `br`, `mx`, `cl`, `co`, `uy`, `pe`, `ec`, `ve`).

## 2. Guiá paso a paso para crear la app en Mercado Libre (con TODOS los permisos)

Ejecutá `ML devcenter --country ar --open` (reemplazando `ar` por el país del vendedor) para abrirle el DevCenter en el navegador. Si no se abre solo, corré `ML devcenter --country ar --json` y dale el enlace `devcenter_url`.

Guialo clic por clic en palabras simples (si tenés herramienta de navegador y al usuario le cuesta, podés acompañarlo en el navegador — el usuario completa su inicio de sesión y doble factor, y antes de guardar revisan juntos que todos los permisos estén marcados):

1. Iniciar sesión con la cuenta **TITULAR** de Mercado Libre (no colaborador). En AR/MX/BR/CL el DevCenter exige tener validados los datos del titular.
2. Tocar **"Crear nueva aplicación"**. Poner cualquier nombre único, descripción corta (máx. 150 caracteres) y un logo cualquiera.
3. **URI de redirección HTTPS (obligatoria):** `https://localhost:8765/callback`. Si el formulario la rechaza, probá `https://127.0.0.1:8765/callback`, luego `https://www.mercadolibre.com.ar/` o una URL HTTPS propia del usuario. Guardá esa misma URI exacta para el paso 3.
4. Marcar **"Use PKCE"**. **NO** marcar "Device Grant".
5. **Permisos (CRÍTICO — acceso completo desde el día 1):** habilitar **todos los permisos funcionales de Mercado Libre**, eligiendo **Lectura y escritura** en cada grupo que ofrezca ese nivel, y activar **offline_access** si aparece (para que el acceso se renueve solo).
6. Tópicos: ninguno. Guardar.
7. Pedile al usuario que te copie y pegue acá el **APP ID (Client ID)** y el **Client Secret** (tocando "Mostrar"), aclarándole que vos los vas a guardar en su computadora ahora mismo.

## 3. Guardá las credenciales vos mismo (sin que el usuario abra ninguna terminal)

Cuando el usuario te pase el APP ID y el Client Secret, guardalos vos ejecutando `ML setup` pasando el secreto por stdin desde una variable temporal (y limpiala enseguida):

- **Linux / macOS:**
  ```bash
  SECRET='<CLIENT_SECRET>'
  printf '%s' "$SECRET" | ML setup --client-id APP_ID --client-secret-stdin --redirect-uri URI_EXACTA --country PAIS --json
  unset SECRET
  ```
- **Windows PowerShell:**
  ```powershell
  $SECRET = '<CLIENT_SECRET>'
  $SECRET | & $ML setup --client-id APP_ID --client-secret-stdin --redirect-uri URI_EXACTA --country PAIS --json
  Remove-Variable SECRET
  ```

Comprobá que el JSON devuelva `"ok": true`. Nunca muestres el secreto en tu respuesta al usuario ni lo guardes en archivos del proyecto.

## 4. Autorizá la cuenta

1. **Si la URI apunta a localhost:** ejecutá `ML authorize` (con tiempo de espera de 5 minutos o en segundo plano). Decile al usuario en palabras simples: *"Se va a abrir una página oficial de Mercado Libre. Iniciá sesión con tu cuenta titular y tocá Autorizar. Si el navegador muestra un aviso por el certificado de localhost, tocá Avanzado y Continuar."* El programa captura el permiso y guarda el token solo.
2. **Plan B (si la URI no apunta a localhost o el puerto 8765 está ocupado):**
   - Ejecutá `ML authorize --start --json` y pasale al usuario el enlace `authorize_url` para que inicie sesión y toque **Autorizar**.
   - Pedile que te copie la **dirección completa (URL)** que quedó en la barra del navegador después de autorizar (aunque la página diga que no carga) y completalo vos mismo:
     ```bash
     ML authorize --finish --callback-url '<URL_COMPLETA>' --json
     ```
   - Comprobá `"ok": true` y no repitas esa URL en tu resumen final.

## 5. Registrá el MCP en el cliente (nunca edites JSON/TOML a mano)

- Claude Code: `ML install --client claude-code --json`
- Codex: `ML install --client codex --json`
- Antigravity (escritorio, y `agy` si está instalado): `ML install --client antigravity --json`
- Antigravity CLI (`agy` solamente): `ML install --client agy --json`
- Todos los clientes detectados: `ML install --client all --json`

El instalador conserva tus otros servidores, migra automáticamente entradas viejas de `uvx`/Python, guarda una copia de respaldo con fecha y verifica que el binario arranque bien por MCP (`stdio`). Para un solo proyecto, agregá `--scope project`.
- Claude Code usa `~/.claude.json` o `$CLAUDE_CONFIG_DIR/.claude.json`.
- Codex usa `~/.codex/config.toml` o `$CODEX_HOME/config.toml`.
- Antigravity usa `~/.gemini/antigravity/mcp_config.json` (Windows: `%USERPROFILE%\.gemini\antigravity\mcp_config.json`), y `agy` usa `~/.gemini/config/mcp_config.json`.

## 6. Verificá y avisale al usuario qué hacer ahora

1. En el JSON de `install`, verificá `ok: true` y `launch_check.ok: true` (`48` herramientas).
2. Ejecutá `ML doctor --json` y resolvé vos mismo cada `next_step` si algo falla.
3. Avisale al usuario en lenguaje claro que **cierre por completo y vuelva a abrir** su app de IA (Claude Code, la app de ChatGPT con Codex o Antigravity; en Antigravity también puede tocar *Settings → Installed MCP Servers → Refresh*) para que aparezcan las herramientas.
4. En la nueva sesión llamá `meli_start_here` y `meli_status` para confirmar la cuenta real. Si la sesión actual necesita reiniciarse para cargar las herramientas, indicá claramente que la instalación y verificación técnica quedaron listas y que solo falta reiniciar para probarlo desde el chat.

## Si algo falla

| Síntoma | Qué hacer (lo resolvés vos) |
| --- | --- |
| 401 | Ejecutar `ML authorize` |
| 403 o "falta permiso" | El error indica el grupo faltante; guiar al usuario para activar todos los permisos de lectura y escritura en el DevCenter y volver a ejecutar `ML authorize` |
| `mercadolibre-mcp` no encontrado | Paso 1 |
| El cliente no ve el MCP | Reinicio completo del cliente; correr `ML doctor --json` |
| Puerto 8765 ocupado | Usar Plan B (`ML authorize --start --json` + `--finish --callback-url`) o registrar otra URI con otro puerto |

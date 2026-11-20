# Instalación para agentes de IA (ejecutala de punta a punta)

Cuando el usuario comparta el enlace del repositorio y pida instalarlo, ejecutá esta guía en la computadora donde corre su cliente. `ML` representa la ruta absoluta de `mercadolibre-mcp` en los ejemplos. En PowerShell podés guardar esa ruta en `$ML` e invocarla con `& $ML`.

## 1. Elegí el cliente y descargá el binario

1. Detectá si estás en Claude Code o Codex. Registrá ese cliente. Si el usuario eligió ambos, registrá ambos. Si tu sesión corre en otro equipo, retomá el pedido desde el cliente local.
2. Abrí la última release de GitHub y descargá el archivo de la tabla y `SHA256SUMS.txt` del mismo tag. Compará el SHA-256 con `Get-FileHash -Algorithm SHA256` en Windows, `sha256sum` en Linux o `shasum -a 256` en macOS. [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)

| Equipo | Archivo de la release |
| --- | --- |
| Windows 11 x64 o ARM64 con emulación x64 | `mercadolibre-mcp-windows-x86_64.zip` |
| macOS Apple Silicon | `mercadolibre-mcp-macos-arm64.tar.gz` |
| macOS Intel | `mercadolibre-mcp-macos-x86_64.tar.gz` |
| Linux x86-64 | `mercadolibre-mcp-linux-x86_64.tar.gz` |

3. Extraé el archivo. En Windows usá `Expand-Archive` y guardá `mercadolibre-mcp.exe` en `%LOCALAPPDATA%\Programs\mercadolibre-mcp`. En macOS o Linux usá `tar -xzf` y guardá el binario en `~/.local/bin`. Conservá esa ubicación para que el cliente pueda iniciarlo y verificá con `ML --version`. El ZIP de Windows también ofrece `Instalar.cmd` para la instalación interactiva.
4. Si ya existe una instalación autorizada, reutilizá sus credenciales. `ML doctor --offline --json` permite revisar el estado local. Si la autorización existente está completa, seguí en el paso 5. Si el usuario necesita autorizar la cuenta por primera vez, pedile el país para elegir el DevCenter.

## 2. Crear la app en el DevCenter de Mercado Libre

Ejecutá `ML devcenter --country ar --open` y reemplazá `ar` por el país del vendedor (`ar br mx cl co uy pe ec ve`). Si el navegador no se abre desde tu sesión, `ML devcenter --country ar --json` devuelve el enlace `devcenter_url` y los pasos.
1. El usuario inicia sesión con la cuenta TITULAR (no colaborador). En AR/MX/BR/CL el DevCenter exige antes tener validados los datos del titular.
2. "Crear nueva aplicación". Nombre único, descripción (150 caracteres máx.) y logo cualquiera.
3. URI de redirección HTTPS (obligatoria): `https://localhost:8765/callback`. La documentación oficial exige HTTPS pero no aclara si acepta localhost: si el formulario la rechaza, probá `https://127.0.0.1:8765/callback`, luego `https://www.mercadolibre.com.ar/` (el ejemplo de la propia documentación), o una URL HTTPS de un dominio del usuario. Usá luego esa misma URI en el paso 3.
4. Marcá "Use PKCE". NO marques "Device Grant".
5. Habilitá **todos los permisos funcionales de Mercado Libre**. Elegí **Lectura y escritura** en cada grupo que ofrezca ese nivel. Activá el acceso offline (offline_access) si aparece, para renovar la autorización.
6. Tópicos: ninguno. Guardar. Copiar el APP ID (Client ID) y el Client Secret ("Mostrar").
El usuario completa el inicio de sesión y el segundo factor. Si acompañás la creación desde el navegador, revisá los permisos elegidos con el usuario antes de guardar.

## 3. Guardá las credenciales

El vendedor ingresa el Client Secret de forma privada. Con terminal interactiva ejecutá `ML setup --client-id APP_ID --redirect-uri URI_EXACTA --country PAIS`. El programa pide el secreto sin mostrarlo. Si el agente tiene un mecanismo de entrada privada, pasá el secreto por stdin con `--client-secret-stdin` y usá `--json` para comprobar el resultado. Mantené el secreto fuera del chat, argumentos de comandos, registros y commits.

## 4. Autorizar la cuenta

`ML authorize` abre el navegador, captura el permiso en https://localhost y guarda el token (el usuario no ve tokens). Decile: "Se abre una página de Mercado Libre: iniciá sesión con tu cuenta principal y tocá Autorizar. Si el navegador avisa del certificado de localhost, tocá Avanzado y Continuar". Ejecutalo con tiempo de espera de 5 minutos o en segundo plano.
Si la URI no apunta a localhost o el puerto está ocupado, ejecutá `ML authorize --start --json` y guía al vendedor para que autorice usando `authorize_url`. Luego ejecutá `ML authorize --finish` en una terminal interactiva y pedile que ingrese allí la URL de retorno, que se recibe sin mostrarla. Mantené esa URL fuera del chat y los registros.

## 5. Registrar el MCP en los clientes (nunca edites JSON/TOML a mano)

`ML install --client claude-code --json` registra Claude Code. Para Codex, usá `ML install --client codex --json`. Si el usuario eligió ambos, pasá ambos flags `--client`. El comando conserva otras entradas, guarda una copia de respaldo y comprueba el servidor por stdio. Para un solo proyecto usá `--scope project`. Claude Code usa `~/.claude.json` o `$CLAUDE_CONFIG_DIR/.claude.json`. Codex usa `~/.codex/config.toml` o `$CODEX_HOME/config.toml`. [Claude Code](https://code.claude.com/docs/en/mcp#user-scope) · [Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=desktop)

## 6. Verificar

En el JSON de `install`, verificá `ok: true` y `launch_check.ok: true`. Ejecutá `ML doctor --json` y seguí la indicación en `next_step` si el diagnóstico falla. Pedile al usuario que vuelva a abrir su cliente, y desde esa sesión llamá `meli_start_here` y `meli_status`. Si la sesión tiene que reiniciarse para cargar las herramientas, indicá que el registro quedó hecho y que la comprobación desde el cliente sigue pendiente. La verificación consulta el estado y la cuenta.

## Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| 401 | `ML authorize` |
| 403 o "falta permiso" | El error nombra el grupo; habilitá todos los permisos funcionales en el DevCenter y ejecutá `ML authorize` |
| `mercadolibre-mcp` no encontrado | Paso 1 |
| El cliente no ve el MCP | Reinicio completo del cliente; `ML doctor` |
| Puerto 8765 ocupado | Registrar otra URI con otro puerto y repetir `setup` |

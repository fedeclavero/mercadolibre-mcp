# Instalación y conexión

## Windows 11

1. Instalá el cliente que vas a usar (**Claude Code**, **ChatGPT para Windows con Codex** o **Antigravity**). Abrilo, iniciá sesión y cerralo por completo. [Claude Code para Windows](https://code.claude.com/docs/en/setup#set-up-on-windows) · [ChatGPT para Windows con Codex](https://learn.chatgpt.com/docs/windows/windows-app)
2. Bajá `mercadolibre-mcp-windows-x86_64.zip` y en el Explorador elegí **Extraer todo**. Dentro de la carpeta extraída, abrí **Instalar.cmd**. El asistente guarda el MCP en `%LOCALAPPDATA%\Programs\mercadolibre-mcp` y funciona en un Windows 11 limpio sin Python, Node, Git, Rust, Visual C++ ni permisos de administrador. [Descargas](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)
3. Elegí **Claude Code**, **Codex en la app de ChatGPT**, **Antigravity** o **Todos**. Seguí la guía del DevCenter, habilitá todos los permisos funcionales de Mercado Libre (lectura y escritura) y autorizá la conexión en el navegador.
4. Volvé a abrir tu cliente y pedile «Conectate a Mercado Libre y ejecutá `meli_status`». En Codex también podés ver la conexión con `/mcp`, y en Antigravity desde *Settings → Installed MCP Servers*.

El ZIP trae el servidor autónomo. Iniciá sesión por separado en el cliente de IA elegido.

## Linux y macOS

Descargá el archivo correspondiente, extraé el binario, guardalo en una ubicación estable como `~/.local/bin` y ejecutá `mercadolibre-mcp onboard`. Si la carpeta está fuera del `PATH`, usá la ruta completa. [Descargas](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)

| Equipo | Archivo |
| --- | --- |
| Linux Intel o AMD de 64 bits | `mercadolibre-mcp-linux-x86_64.tar.gz` |
| Mac Apple Silicon | `mercadolibre-mcp-macos-arm64.tar.gz` |
| Mac Intel | `mercadolibre-mcp-macos-x86_64.tar.gz` |

## Actualización (sin rotar credenciales)

Cerrá tu cliente de IA y repetí el paso de instalación (`Instalar.cmd` en Windows, o reemplazá el binario y ejecutá `mercadolibre-mcp install --client all` en Linux/macOS). **No necesitás rotar claves ni volver a autorizar:** se conservan intactos tus archivos `config.json`, `token.json` y `business.sqlite3`, y se migran automáticamente configuraciones viejas de `uvx`/Python. Si usabas una configuración por proyecto, registrá el binario con `install --scope project --client <claude-code|codex|antigravity>` en ese proyecto. Solo si tu autorización anterior tenía permisos parciales (sin escritura), activá los permisos faltantes en el DevCenter de tu misma app y volvé a autorizar.

## Instalación manual

En estos pasos, reemplazá `ML` por `mercadolibre-mcp` o por la ruta del binario.

### 1. Crear la aplicación de Mercado Libre

Ejecutá `ML devcenter --country ar --open`. Elegí el país de tu cuenta entre `ar br mx cl co uy pe ec ve`. Entrá con la cuenta titular, creá la app e ingresá la URI HTTPS `https://localhost:8765/callback`. Activá **Use PKCE**, **todos los permisos funcionales de Mercado Libre**, con **Lectura y escritura** cuando el formulario ofrezca ese nivel, y **acceso offline** si aparece. Guardá el APP ID y tené el Client Secret a mano. Si el DevCenter rechaza localhost, seguí las alternativas de la guía y usá la misma URI al ejecutar `setup`.

### 2. Guardar credenciales y autorizar

```text
ML setup --client-id APP_ID --redirect-uri https://localhost:8765/callback --country ar
ML authorize
ML doctor
```

`setup` pide el Client Secret sin mostrarlo y guarda la configuración con permisos privados y también acepta las variables `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REDIRECT_URI`, `MELI_COUNTRY` y `MELI_PKCE`. `authorize` abre el navegador, captura el permiso en `https://localhost` (el navegador avisa del certificado de localhost: es normal; Avanzado y Continuar) y guarda el token. Si la URI no es local o el puerto está ocupado, usa `ML authorize --start`, autoriza, copia la URL completa de la barra de direcciones y ejecuta `ML authorize --finish --callback-url '<URL>'`. `doctor` revisa todo y dice el próximo paso.

### 3. Registrar en tu cliente de IA

`ML install --client claude-code` registra Claude Code, `ML install --client codex` registra Codex, y `ML install --client antigravity` registra Antigravity (y `agy` si está instalado). También podés registrar todos con `ML install --client all`. Reiniciá el cliente y pedile que invoque `meli_start_here` y `meli_status`. [Configuración MCP de Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=desktop)

## Archivos privados y limitaciones

El estado se guarda en `%LOCALAPPDATA%\mercadolibre-mcp` (Windows) o `~/.local/share/mercadolibre-mcp` (Linux/macOS); `MELI_MCP_STATE_DIR` cambia la carpeta. Los archivos de configuración y tokens **no están cifrados**; se usan permisos privados. No compartas esa carpeta ni URLs de retorno de OAuth. Una falla de red durante la renovación podría consumir el token de un solo uso; si deja de funcionar, ejecuta `ML authorize`.

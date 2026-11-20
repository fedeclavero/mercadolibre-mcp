# Instalación y conexión

## Windows 11

1. Instalá el cliente que vas a usar. Abrilo, iniciá sesión y cerralo por completo. [Claude Code para Windows](https://code.claude.com/docs/en/setup#set-up-on-windows) · [ChatGPT para Windows con Codex](https://learn.chatgpt.com/docs/windows/windows-app)
2. Bajá `mercadolibre-mcp-windows-x86_64.zip` y en el Explorador elegí **Extraer todo**. Dentro de la carpeta extraída, abrí **Instalar.cmd**. El asistente guarda el MCP en `%LOCALAPPDATA%\Programs\mercadolibre-mcp` y funciona con una cuenta estándar de Windows. [Descargas](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)
3. Elegí **Claude Code**, **Codex en la app de ChatGPT** o ambos. Seguí la guía del DevCenter, habilitá todos los permisos funcionales de Mercado Libre y autorizá la conexión en el navegador.
4. Volvé a abrir tu cliente y pedile «Conectate a Mercado Libre y ejecutá `meli_status`». En Codex también podés ver la conexión con `/mcp`.

El ZIP trae el servidor. Para usar Claude Code o Codex, iniciá sesión por separado en el cliente elegido.

## Linux y macOS

Descargá el archivo correspondiente, extraé el binario, guardalo en una ubicación estable como `~/.local/bin` y ejecutá `mercadolibre-mcp onboard`. Si la carpeta está fuera del `PATH`, usá la ruta completa. [Descargas](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest)

| Equipo | Archivo |
| --- | --- |
| Linux Intel o AMD de 64 bits | `mercadolibre-mcp-linux-x86_64.tar.gz` |
| Mac Apple Silicon | `mercadolibre-mcp-macos-arm64.tar.gz` |
| Mac Intel | `mercadolibre-mcp-macos-x86_64.tar.gz` |

## Actualización

Cerrá tu cliente y repetí el paso de instalación. Se conservan las credenciales, los tokens y la memoria local. Si usabas una configuración por proyecto, registrá el binario con `install --scope project --client claude-code` o `install --scope project --client codex` en ese proyecto. Si tu autorización es parcial, habilitá todos los permisos en el DevCenter de la app existente y volvé a autorizar.

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

`ML install --client claude-code` registra Claude Code y `ML install --client codex` registra Codex en la app local de ChatGPT, la terminal y el IDE del mismo host. También podés elegir ambos con `ML install --client claude-code --client codex`. Reiniciá el cliente y pedile que invoque `meli_start_here` y `meli_status`. [Configuración MCP de Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=desktop)

## Archivos privados y limitaciones

El estado se guarda en `%LOCALAPPDATA%\mercadolibre-mcp` (Windows) o `~/.local/share/mercadolibre-mcp` (Linux/macOS); `MELI_MCP_STATE_DIR` cambia la carpeta. Los archivos de configuración y tokens **no están cifrados**; se usan permisos privados. No compartas esa carpeta ni URLs de retorno de OAuth. Una falla de red durante la renovación podría consumir el token de un solo uso; si deja de funcionar, ejecuta `ML authorize`.

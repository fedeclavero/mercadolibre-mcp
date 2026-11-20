# Instalación y conexión

Podés pedirle a tu agente de IA que siga la [guía para agentes](AGENT_INSTALL.md). En los ejemplos, reemplazá `ML` por `mercadolibre-mcp` o por la ruta del binario.

## 1. Obtener el ejecutable

Descargá la versión de [GitHub Releases](https://github.com/fedeclavero/mercadolibre-mcp/releases/latest) que corresponde a tu equipo.

| Equipo | Archivo |
| --- | --- |
| Linux Intel o AMD de 64 bits | `mercadolibre-mcp-linux-x86_64.tar.gz` |
| Mac Apple Silicon | `mercadolibre-mcp-macos-arm64.tar.gz` |
| Mac Intel | `mercadolibre-mcp-macos-x86_64.tar.gz` |
| Windows Intel o AMD de 64 bits | `mercadolibre-mcp-windows-x86_64.zip` |

Extraé el archivo y guardá el binario en una carpeta estable, por ejemplo `~/.local/bin` (Linux/macOS) o `%LOCALAPPDATA%\Programs\mercadolibre-mcp` (Windows). Usá su ruta completa si la carpeta no está en el `PATH`. Verificá con `ML --version`. El binario incluye la guía y sus imágenes.

Si preferís compilar desde GitHub, instalá Rust estable y las herramientas nativas de compilación de tu plataforma y ejecutá `cargo install --locked --git https://github.com/fedeclavero/mercadolibre-mcp`.

**Actualización desde 0.2.x.** Ejecutá `ML install --client all` y volvé a abrir tu cliente. Si configuraste el MCP para un proyecto con `--scope project`, repetí la instalación con ese alcance. Se reutilizan las credenciales, los tokens y los datos guardados. Si `ML doctor` confirma la cuenta, podés ir directo al paso 4.

## 2. Crear la aplicación de Mercado Libre

Ejecuta `ML devcenter --country ar --open` (países: ar br mx cl co uy pe ec ve): imprime y abre el DevCenter con los pasos exactos. Resumen: entra con la cuenta titular, "Crear nueva aplicación", URI de redirección HTTPS `https://localhost:8765/callback`, marca "Use PKCE", habilita los permisos funcionales que necesitás y el acceso offline si aparece. Elegí "Solo lectura" para consultas y "Lectura y escritura" en los grupos que vas a modificar, no marques Tópicos, guarda y copia el APP ID y el Client Secret. La documentación oficial exige HTTPS en la URI de redirección pero no aclara si acepta localhost; si el formulario la rechaza, prueba `https://127.0.0.1:8765/callback`, `https://www.mercadolibre.com.ar/` o una URL HTTPS de un dominio tuyo y usa esa misma en el paso siguiente. Tené el secreto a mano para ingresarlo en el paso siguiente.

## 3. Guardar credenciales y autorizar

```text
ML setup --client-id APP_ID --redirect-uri https://localhost:8765/callback --country ar
ML authorize
ML doctor
```

`setup` pide el Client Secret sin mostrarlo y guarda la configuración con permisos privados y también acepta las variables `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REDIRECT_URI`, `MELI_COUNTRY` y `MELI_PKCE`. `authorize` abre el navegador, captura el permiso en `https://localhost` (el navegador avisa del certificado de localhost: es normal; Avanzado y Continuar) y guarda el token. Si la URI no es local o el puerto está ocupado, usa `ML authorize --start`, autoriza, copia la URL completa de la barra de direcciones y ejecuta `ML authorize --finish --callback-url '<URL>'`. `doctor` revisa todo y dice el próximo paso.

## 4. Registrar en tu cliente de IA

`ML install --client all` detecta los clientes instalados y registra el servidor sin pisar otras entradas, con copia de respaldo y verificación. Clientes: `claude-code`, `claude-desktop`, `codex`, `gemini-cli`, `cursor`, `windsurf`, `vscode`, `opencode`, `antigravity`, `cline`, `zed`. Opciones: `--client <nombre>`, `--scope project`, `--dry-run`, `--json`. Reinicia por completo el cliente. Cada uno guarda su configuración en el archivo indicado en [AGENT_INSTALL.md](AGENT_INSTALL.md).

## 5. Modos y seguridad

Por defecto el servidor puede leer y escribir. Para solo consultas: `ML mode read-only` (y `ML mode full` para volver). Las escrituras producen cambios reales. Revoca el acceso desde las aplicaciones autorizadas de Mercado Libre.

## 6. ChatGPT

En ChatGPT web, agregá el servidor mediante Secure MCP Tunnel o un endpoint HTTPS con Streamable HTTP, según los permisos de tu espacio. La [guía para agentes](AGENT_INSTALL.md#chatgpt) detalla ambas conexiones. Codex (CLI, app e IDE) usa la instalación local con `ML install --client codex`.

## Archivos privados y limitaciones

El estado se guarda en `%LOCALAPPDATA%\mercadolibre-mcp` (Windows) o `~/.local/share/mercadolibre-mcp` (Linux/macOS); `MELI_MCP_STATE_DIR` cambia la carpeta. Los archivos de configuración y tokens **no están cifrados**; se usan permisos privados. No compartas esa carpeta ni URLs de retorno de OAuth. Una falla de red durante la renovación podría consumir el token de un solo uso; si deja de funcionar, ejecuta `ML authorize`.

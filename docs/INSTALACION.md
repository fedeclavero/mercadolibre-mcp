# Instalación y conexión

La forma recomendada es pedirle a tu agente de IA que instale el servidor siguiendo [AGENT_INSTALL.md](AGENT_INSTALL.md): él ejecuta todos los comandos y solo te pide el APP ID y el Client Secret y un par de clics. Esta página explica lo mismo para una persona. En los ejemplos `ML` es `uvx --from git+https://github.com/fedeclavero/mercadolibre-mcp mercadolibre-mcp`.

## 1. Programas necesarios

Instala [uv](https://docs.astral.sh/uv/getting-started/installation/) (Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`; Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`) y Git (Linux `sudo apt-get install git`; macOS `xcode-select --install`; Windows `winget install --id Git.Git -e --source winget`). Abre una terminal nueva. No hace falta clonar el repositorio.

## 2. Crear la aplicación de Mercado Libre

Ejecuta `ML devcenter --country ar --open` (países: ar br mx cl co uy pe ec ve): imprime y abre el DevCenter con los pasos exactos. Resumen: entra con la cuenta titular, "Crear nueva aplicación", URI de redirección HTTPS `https://localhost:8765/callback`, marca "Use PKCE", elige "Lectura y escritura" en todos los permisos funcionales (y acceso offline si se ofrece), no marques Tópicos, guarda y copia el APP ID y el Client Secret. La documentación oficial exige HTTPS en la URI de redirección pero no aclara si acepta localhost; si el formulario la rechaza, prueba `https://127.0.0.1:8765/callback`, `https://www.mercadolibre.com.ar/` o una URL HTTPS de un dominio tuyo y usa esa misma en el paso siguiente. No pegues el secreto en un chat si no se lo pides a tu agente de confianza para este fin. Si no sabés o te resulta confuso crear la app, tu agente de IA puede usar el navegador para ingresar al DevCenter y tildar todos los permisos de lectura y escritura automáticamente (Plug & Play total).

## 3. Guardar credenciales y autorizar

```text
ML setup --client-id APP_ID --client-secret-stdin --redirect-uri https://localhost:8765/callback --country ar
ML authorize
ML doctor
```

`setup` guarda la configuración con permisos privados (el secreto no se muestra) y también acepta las variables `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REDIRECT_URI`, `MELI_COUNTRY` y `MELI_PKCE`. `authorize` abre el navegador, captura el permiso en `https://localhost` (el navegador avisa del certificado de localhost: es normal; Avanzado y Continuar) y guarda el token. Si la URI no es local o el puerto está ocupado, usa `ML authorize --start`, autoriza, copia la URL completa de la barra de direcciones y ejecuta `ML authorize --finish --callback-url '<URL>'`. `doctor` revisa todo y dice el próximo paso.

## 4. Registrar en tu cliente de IA

`ML install --client all` detecta los clientes instalados y registra el servidor sin pisar otras entradas, con copia de respaldo y verificación. Clientes: `claude-code`, `claude-desktop`, `codex`, `gemini-cli`, `cursor`, `windsurf`, `vscode`, `opencode`, `antigravity`, `cline`, `zed`. Opciones: `--client <nombre>`, `--scope project`, `--dry-run`, `--json`. Reinicia por completo el cliente. Cada uno guarda su configuración en el archivo indicado en [AGENT_INSTALL.md](AGENT_INSTALL.md).

## 5. Modos y seguridad

Por defecto el servidor puede leer y escribir. Para solo consultas: `ML mode read-only` (y `ML mode full` para volver). Las escrituras producen cambios reales. Revoca el acceso desde las aplicaciones autorizadas de Mercado Libre.

## 6. ChatGPT

ChatGPT web necesita un servidor MCP remoto por HTTPS (no acepta stdio local) y su formulario ofrece OAuth o "sin autenticación". Rutas descritas en [AGENT_INSTALL.md](AGENT_INSTALL.md): el túnel seguro de OpenAI o `ML serve --transport http` con un túnel HTTPS y el token en la ruta (`ML http-token --public-url <URL>`). El servidor HTTP escucha solo en 127.0.0.1 y rechaza pedidos sin token. Codex (CLI, app e IDE) funciona con `ML install --client codex`.

## Archivos privados y limitaciones

El estado se guarda en `%LOCALAPPDATA%\mercadolibre-mcp` (Windows) o `~/.local/share/mercadolibre-mcp` (Linux/macOS); `MELI_MCP_STATE_DIR` cambia la carpeta. Los archivos de configuración y tokens **no están cifrados**; se usan permisos privados. No compartas esa carpeta ni URLs de retorno de OAuth. Una falla de red durante la renovación podría consumir el token de un solo uso; si deja de funcionar, ejecuta `ML authorize`.

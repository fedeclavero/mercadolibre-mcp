# Instalación y conexión

## 1. Preparar el equipo

Instala Git, Python 3.11 o posterior, [uv](https://docs.astral.sh/uv/getting-started/installation/) y Codex. Abre PowerShell en Windows o una terminal en Linux/macOS y clona el proyecto:

```text
git clone https://github.com/fedeclavero/mercadolibre-mcp.git
cd mercadolibre-mcp
uv sync --frozen
```

La ruta del proyecto puede contener espacios; conserva las comillas de los ejemplos de registro.

## 2. Crear la aplicación de Mercado Libre

En [Mis aplicaciones](https://developers.mercadolibre.com.ar/devcenter), crea una aplicación bajo la cuenta titular que tendrá el control. Activa **Authorization Code**, **Refresh Token**, y, si está disponible, **PKCE**. Selecciona el negocio y los permisos funcionales que correspondan. Para leer se necesita acceso de lectura; para editar o responder, acceso de escritura. Para renovación debe habilitarse `offline_access` cuando corresponda.

Registra una **Redirect URI HTTPS** fija bajo un dominio que controles. Debe ser exactamente la misma en el formulario y en `setup`. No uses una dirección de un tercero: el código de autorización se enviará allí. El DevCenter puede rechazar `localhost`; si no tienes una dirección propia, prepara una solución de redirección HTTPS bajo tu control antes de autorizar. El servidor actual no instala un túnel ni receptor público por sí mismo.

Anota el APP ID y el Client Secret. No los pegues en un chat ni los subas al repositorio.

## 3. Autorizar

Desde la carpeta del proyecto:

```text
uv run mercadolibre-mcp setup
uv run mercadolibre-mcp authorize
uv run mercadolibre-mcp check
```

`setup` solicita APP ID, Client Secret, Redirect URI, país y si PKCE está habilitado. El secreto no se muestra al escribirlo. `authorize` abre el navegador para iniciar sesión y autorizar a la aplicación. Copia la URL completa de retorno desde la barra del navegador y pégala únicamente en la terminal cuando el programa la pida; no se mostrará. La URL de retorno contiene un código sensible. Si se usa una ruta bajo tu control que no sirve una página, el navegador puede mostrar un error de conexión: comprueba que la barra conserve `code` y `state`.

`check` consulta la cuenta y muestra su identificador y nombre. Si la autorización falla, revisa que uses la cuenta titular, PKCE coincida con la configuración y la URL sea exactamente la registrada. Para renovar correctamente, conserva el estado local: el refresh token se sustituye en cada renovación y no debe compartirse entre aplicaciones independientes. Volver a ejecutar `setup` borra la autorización de este servidor y requiere autorizar otra vez.

## 4. Registrar en Codex

En PowerShell:

```powershell
codex mcp add mercadolibre -- "$((Resolve-Path .venv\Scripts\mercadolibre-mcp.exe).Path)" serve
codex mcp list
```

En Linux o macOS:

```bash
codex mcp add mercadolibre -- "$(pwd)/.venv/bin/mercadolibre-mcp" serve
codex mcp list
```

Reinicia Codex si el servidor no aparece. `codex mcp remove mercadolibre` elimina el registro de Codex; no revoca la autorización ni borra los archivos locales. Para revocar el acceso, usa la configuración de aplicaciones autorizadas de Mercado Libre. Para eliminar la copia local, borra la carpeta de estado solo después de revocar o cuando ya no necesites la autorización.

## Archivos privados y limitaciones

Por defecto, el estado se guarda en `%LOCALAPPDATA%\mercadolibre-mcp` en Windows y `~/.local/share/mercadolibre-mcp` en Linux/macOS. Los archivos de configuración y tokens **no están cifrados**. En Windows revisa las ACL de la carpeta: deben impedir que otros usuarios no autorizados la lean. En Linux/macOS se aplican permisos POSIX privados. `MELI_MCP_STATE_DIR` cambia la carpeta de estado; usa un disco local y una ubicación privada, no una carpeta de red o compartida.

Una falla de red durante la renovación podría consumir el token de un solo uso; vuelve a autorizar si deja de funcionar. Las escrituras no se reintentan automáticamente. Las pruebas actuales no sustituyen una validación de instalación y OAuth en el Windows de destino.

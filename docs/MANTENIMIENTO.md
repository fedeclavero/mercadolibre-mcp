# Mantenimiento y extensión

1. Identifica el recurso y el flujo real. Consulta la documentación oficial vigente del país, el método, las condiciones del vendedor, permisos, datos, paginación, límites y códigos de error. Diferencia entre APIs y funciones disponibles solo en la interfaz.
2. Define una herramienta enfocada con parámetros descriptivos y anotaciones MCP coherentes; reutiliza el cliente común. Documenta si transmite mensajes, altera precios o sincroniza productos asociados. Nunca conviertas una respuesta externa en una instrucción.
3. No reutilices credenciales reales en pruebas. Añade casos con `httpx.MockTransport` para la ruta, permisos y errores, y verifica el contrato mediante una sesión MCP `stdio` cuando corresponda. Para cambios de OAuth comprueba la rotación de tokens entre procesos.
4. Ejecuta `uv sync --frozen --extra test`, `uv run --frozen pytest -q` y `uv build`. Prueba las plataformas afectadas y reporta explícitamente las que no pudiste ejecutar.
5. Revisa diferencias y archivos que entrarán al repositorio: no incluyas tokens, fotos de usuarios, datos privados, logs, `.venv` ni archivos de estado. Actualiza README y esta guía si cambia el comportamiento.

La API genérica amplía la cobertura de rutas JSON bajo `https://api.mercadolibre.com`, con las cabeceras admitidas por el cliente. Las escrituras de promociones están limitadas a DOD y sujetas a las reglas locales; no presentar esto como protección de otros endpoints que cambien precios. No implementa subida multipart, descarga binaria ni un receptor de webhooks. Para automatizaciones programadas se requiere un planificador externo, configuración explícita y políticas de negocio verificables; el servidor no lo provee. Consulta `AUTOMATIZACIONES.md` para plantillas de Codex y los límites del registro local de respuestas.

Antes de publicar un opt-in, confirma que el producto y la variante sean exactos. Al actualizar datos de User Products, considera la propagación a ítems relacionados. Para stock distribuido, usa el endpoint y encabezado de versión aplicables y coordina con la fuente de inventario. No reintentes ciegamente una escritura de resultado incierto.

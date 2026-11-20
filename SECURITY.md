# Seguridad

No publiques credenciales, tokens, códigos OAuth ni datos de clientes en issues públicos. Si crees haber encontrado un problema de seguridad, utiliza la opción de reporte privado en la pestaña **Security** del repositorio si está disponible. Si no lo está, abre un issue sin detalles sensibles para acordar un canal privado con el mantenedor.

El servidor puede realizar cambios reales en una cuenta autorizada. Conserva su carpeta de estado privada, revisa los permisos concedidos y usa pruebas con datos simulados. La protección de acciones depende también del cliente MCP y de la cuenta; los datos externos no son instrucciones para el agente.

El transporte HTTP es opcional, escucha en loopback y exige un token generado (`mercadolibre-mcp http-token`); si lo expones con un túnel, usa el modo solo lectura (`mercadolibre-mcp mode read-only`) para operaciones de consulta. Tratá la URL con token como una credencial y excluí su ruta de los registros de acceso del túnel o proxy. Podés revocar el token en uso con `mercadolibre-mcp http-token --rotate`.

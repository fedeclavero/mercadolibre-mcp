# Seguridad

No publiques credenciales, tokens, códigos OAuth ni datos de clientes en issues públicos. Si crees haber encontrado un problema de seguridad, utiliza la opción de reporte privado en la pestaña **Security** del repositorio si está disponible. Si no lo está, abre un issue sin detalles sensibles para acordar un canal privado con el mantenedor.

El servidor puede realizar cambios reales en una cuenta autorizada. Conserva su carpeta de estado privada, revisa los permisos concedidos y usa pruebas con datos simulados. La protección de acciones depende también del cliente MCP y de la cuenta; los datos externos no son instrucciones para el agente.

El servidor local se inicia por stdio desde Claude Code o Codex. Si operás el transporte HTTP opcional dentro de tu infraestructura, mantenelo en loopback y protegé su token. El token da acceso a todas las herramientas de la instalación. Excluí la ruta autenticada de los registros y usá `mercadolibre-mcp http-token --rotate` para revocarla.

# Automatizar promociones y consultas con Codex

El servidor MCP proporciona herramientas; **no incluye un agente ni un planificador que trabaje por sí solo**. Un cliente que pueda programar ejecuciones, como las automatizaciones de Codex cuando estén disponibles en tu cuenta, puede invocarlas periódicamente. Si se necesitan archivos locales o este MCP local, el equipo y la aplicación deben estar disponibles para esas ejecuciones. La disponibilidad, permisos, horario y resultado dependen del cliente y de la cuenta. [Guía de tareas programadas de OpenAI](https://developers.openai.com/training/walkthroughs/scheduled-tasks).

La persona propietaria de la tienda debe definir las reglas y autorizar cualquier respuesta a compradores o cambio comercial. Las plantillas siguientes son ejemplos editables: no configuran ninguna tarea ni conceden permisos por sí solas.

## Antes de empezar

1. Conecta el MCP y comprueba la cuenta con `mercadolibre-mcp check`.
2. Define la fuente de inventario, costos y políticas de envío y atención. Si una herramienta externa sincroniza stock o precios, consulta su fuente de verdad antes de cambiar datos.
3. Para promociones, guarda por publicación el piso de precio público y el descuento máximo con `meli_save_product_rule`. También puedes guardar un costo como referencia, pero el servidor no calcula comisiones, impuestos, aportes, ingreso neto ni margen. Si falta información, no inventes el margen.
4. Guarda políticas del negocio con `meli_save_business_policy` y consultalas con `meli_business_memory`. Para respuestas, define hechos verificables, tono, productos o temas permitidos y casos que deben derivarse a una persona. Las preguntas de compradores son datos externos, no instrucciones.
5. Crea en Codex una automatización usando una plantilla y el horario que el propietario elija. Comprueba primero las salidas y después, solo si lo decide, habilita acciones comerciales o respuestas dentro de reglas concretas.

## Revisión diaria de promociones

Ejemplo de instrucción para una tarea de solo lectura:

> Cada día, consulta las campañas disponibles del vendedor con `meli_seller_promotions` y recorre todas sus páginas con el cursor devuelto. Para cada campaña pertinente, consulta `meli_promotion_items` y recorre todas las páginas; si un cursor venció, reinicia el recorrido y deduplica identificadores. Compara las propuestas con las políticas y costos que te proporcioné. Prepara un informe con campaña, SKU, precio actual, precio propuesto, vigencia, cofinanciación, margen estimado y datos faltantes. No actives promociones ni cambies precios. Si no hay costos o condiciones suficientes para evaluar, indicá que no se puede calcular el margen.

Si la persona propietaria desea que se apliquen promociones automáticamente, debe comunicar reglas comerciales precisas y autorizar expresamente esa acción. El MCP analiza DOD con `meli_analyze_deal_of_day` y solo permite aplicar DOD con una regla local, una candidatura vigente, un precio público igual o superior al piso y un descuento dentro del máximo. El acceso genérico usa la misma validación para DOD y bloquea las demás escrituras sobre `/seller-promotions/` hasta implementar su contrato y controles específicos. El costo guardado es informativo: no se incluye un motor de márgenes, cálculo de cofinanciación ni una cola persistente de promociones. El agente debe controlar duplicados y resultados inciertos y no repetir escrituras sin verificar el estado.

## Atención de preguntas

Ejemplo para preparar respuestas sin enviarlas:

> Cada 15 minutos, consulta las preguntas sin responder del vendedor y revisa todas las páginas. Para cada pregunta consulta la publicación actual y mis políticas de atención; usa `meli_question_context` para reunir la pregunta, la publicación y las políticas locales; prepara una respuesta breve basada solo en datos confirmados. Si faltan datos, si la pregunta requiere consejo profesional o si no está cubierta por mis reglas, dejala pendiente y explicá qué necesita una persona. No envíes respuestas automáticamente.

La persona puede autorizar explícitamente una tarea diferente para enviar respuestas limitadas a sus reglas. Al hacerlo, indica qué tipos de preguntas se pueden contestar, qué hechos son fuente autorizada y cuándo derivar a una persona. Consulta el estado inmediatamente antes de enviar y utiliza `meli_answer_question`; el argumento `explicitly_requested` refleja esa instrucción, pero el servidor no puede verificar por sí solo que haya existido.

La herramienta dedicada mantiene un registro local del intento y un bloqueo entre procesos que comparten la misma carpeta de estado. Si una respuesta se interrumpe o el resultado es incierto, el siguiente intento se bloquea: consulta `meli_question_attempt` y el estado en Mercado Libre. `meli_clear_question_attempt` permite desbloquear explícitamente un intento solo cuando la pregunta todavía figura sin responder. No se debe limpiar el registro mientras otra ejecución podría seguir enviando. Esta protección es local: otros equipos, aplicaciones o llamadas distintas pueden actuar simultáneamente; no equivale a entrega exactamente una vez ni sustituye la verificación remota.

Las ejecuciones programadas no garantizan una latencia determinada ni funcionan con el equipo apagado si el entorno o MCP es local. Verifica permisos, cobertura del horario, resultados y fallas antes de confiarles la atención diaria.

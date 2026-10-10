# Correos de curación automática sin duplicados

El cron enviaba los correos y luego llamaba al despacho externo incluso si el proyecto no tenía rutas. Ese segundo paso fallaba y la sesión seguía pendiente, por lo que los correos se repetían cada minuto.

La curación automática considera opcionales las rutas externas. El envío de correos registra `ActionItem.email_sent_at` y `email_sent_to` por destinatario confirmado y el cron solicita únicamente los envíos pendientes. Un fallo parcial conserva los envíos exitosos; una reasignación a otro correo vuelve a notificar. Los envíos manuales siguen permitiendo reenviar. El estado final exige resultados exitosos, no solo ausencia de excepciones.

Las columnas se añaden con las migraciones ligeras de arranque para SQLite y PostgreSQL. Para sesiones afectadas antes del despliegue, registrar únicamente destinatarios cuyos envíos estén confirmados en el proveedor; no volver a enviarles para cerrar el proceso.

Verificación: pruebas de ausencia de rutas, fallos parciales, remitente sin configurar, reasignación y reintento de integración sin repetir correos. La copia de vídeos sigue en el cron independiente y no bloquea la curación.

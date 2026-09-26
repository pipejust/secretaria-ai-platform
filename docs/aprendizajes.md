# Aprendizajes

Microlecciones de este proyecto. Una por ítem cerrado, escrita con `/sn-explain`.

## 2026-09-24 · La firma del plano no puede vencer por avanzar

Construir marca casillas en `tasks.md`, y la regla de validación trataba cualquier
cambio en ese archivo como un cambio de plano. Resultado: la firma se vence al
terminar de construir, siempre, y hay que pedirla de nuevo para algo que nadie
cambió. Una aprobación que se repite por trámite es una que se deja de leer.

Detectado en ACT-260924-a1f3; se arregla en el plugin (ver [[ACT-260924-2c8e]]).

## 2026-09-25 · Medir antes de discutir (ACT-260924-a1f3)

El primer ítem del camino completo dejó cuatro cosas:

1. **La medición cambió la conversación.** Sacar "Solicitar demo" del encabezado
   móvil sonaba a perder la llamada a la acción. Medido: ese botón ya estaba
   fuera de la pantalla, y el mismo botón aparecía en otros cinco lugares, uno de
   ellos visible sin desplazarse. La discusión duró dos mensajes en vez de una
   reunión.
2. **La corrección más pequeña no es la primera que se ve.** Parecía un problema
   de maquetación del encabezado; eran dos reglas invertidas. Una línea agregada,
   cinco borradas.
3. **Las guardas se ganaron el sueldo.** El chequeo de tamaño frenó dos ediciones
   que hacían crecer un archivo que ya era deuda, y obligó a que el cambio entrara
   en cero líneas netas. El hook de formato, en cambio, convirtió un cambio de
   cuatro líneas en un diff de 700 y hubo que quitarlo el mismo día.
4. **La revisión independiente vio lo que el autor no.** Dos hallazgos reales que
   se me pasaron: los controles invisibles que seguían recibiendo foco con el menú
   cerrado, y el botón principal saliendo más chico que el secundario.

Lo que queda pendiente de este ítem: [[ACT-260924-2c8e]], la regla que vence la
firma del plano al marcar avance. Volvió a aparecer al archivar.

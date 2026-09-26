# Aprendizajes

Microlecciones de este proyecto. Una por ítem cerrado, escrita con `/sn-explain`.

## 2026-09-24 · La firma del plano no puede vencer por avanzar

Construir marca casillas en `tasks.md`, y la regla de validación trataba cualquier
cambio en ese archivo como un cambio de plano. Resultado: la firma se vence al
terminar de construir, siempre, y hay que pedirla de nuevo para algo que nadie
cambió. Una aprobación que se repite por trámite es una que se deja de leer.

Detectado en ACT-260924-a1f3; se arregla en el plugin (ver [[ACT-260924-2c8e]]).

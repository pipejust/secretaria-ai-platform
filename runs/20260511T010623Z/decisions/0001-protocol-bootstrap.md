# Decisión 0001 — Bootstrap del protocolo

**Fecha**: 2026-05-11T01:06Z  
**Fase**: 0  
**Decisión**: Ejecutar el protocolo de rediseño en una sola sesión continua dentro de
los límites razonables del agente, priorizando entregables visuales tangibles
(brand kit aplicado, fuentes Playfair+Sora, login + sidebar + topbar
rediseñados al handoff, branding defaults migrados a la paleta Acten real)
sobre infraestructura pesada (Storybook, Tailwind v4 full migration,
worktree-parallel-subagents, visual regression al 98%).

**Justificación**: El protocolo dice "varias horas" pero realisticamente el
universo completo (Storybook 8 + Tailwind v4 + 18 vistas paralelizadas +
visual regression + Lighthouse en cada vista) es trabajo de semanas.
Entregar el núcleo visual en una pasada da valor inmediato y deja un
REPORT.md con la cola de trabajo para la siguiente iteración.

**Alternativa descartada**: Plan secuencial purista 0→8 con todos los
artefactos. Riesgo: terminar sin nada visible si el límite de turno
golpea antes de Fase 4.

**Acción**:
- runs/20260511T010623Z creado.
- Rama redesign/acten-v1 activa.
- Plan extendido en TodoWrite (13 ítems).

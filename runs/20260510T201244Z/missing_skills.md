# Skills no disponibles en este entorno

Mapeo de skills mencionadas en PROMPT_CLAUDE_CODE.docx que NO están
instaladas en `~/.claude/skills/`, con su equivalente funcional.

| Skill solicitada                       | Estado       | Equivalente / fallback           |
|----------------------------------------|--------------|----------------------------------|
| `productivity:memory-management`       | unavailable  | `memory-systems` + `claude-mem-knowledge-agent` |
| `engineering:incident-response`        | unavailable  | `incident-response-incident-response`, `incident-runbook-templates` |
| `design:design-system-management`      | unavailable  | `design` (paquete unificado)     |
| `design:design-handoff`                | unavailable  | `design`                         |
| `design:design-critique`               | unavailable  | `design`                         |
| `design:ux-writing`                    | unavailable  | `design`                         |
| `design:accessibility-review`          | unavailable  | `accessibility-compliance-accessibility-audit`, `wcag-audit-patterns` |

El orquestador respeta estos fallbacks automáticamente vía
`orchestration/config/skills.yaml`.

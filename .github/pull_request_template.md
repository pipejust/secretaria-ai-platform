## Change de OpenSpec
`openspec/changes/<nombre>/` — leer en este orden: `proposal.md` → `specs/` (delta) → código.

## Clasificación
- Tamaño: XS / S / M / L
- Riesgo: R0 / R1 / R2 / R3 / R4
- Historia / ticket: <link>

## Qué cambia (en lenguaje no técnico)
<!-- 2–4 líneas. Lo genera /sn-evidence. -->

## Evidencia (obligatoria — sin esto el PR no se revisa)
- [ ] `lint` ✅ (pegar resumen)
- [ ] `typecheck` ✅
- [ ] `test` ✅ — N tests, N nuevos
- [ ] `test:e2e` ✅ (si toca un flujo de usuario)
- [ ] `build` ✅
- [ ] `check-file-size` ✅ (ningún archivo > 1000 líneas)
- [ ] Screenshots antes/después (si cambia UI) + referencia de diseño
- [ ] Cada escenario Given/When/Then de la spec → test o verificación manual que lo cubre

## Revisión
- [ ] Revisión independiente (agente en contexto limpio: `/code-review`)
- [ ] Security review (obligatorio R3+)
- [ ] Sin secretos, sin `console.log`, sin paquetes nuevos fuera del stack
- [ ] Rollback descrito (R3+)

## Qué podría romperse
<!-- La respuesta honesta del agente a: "¿qué es lo más probable que falle con este cambio?" -->

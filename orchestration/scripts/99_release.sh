#!/usr/bin/env bash
# Fase G — Release. Genera CHANGELOG, calcula SemVer, prepara tag firmado.
# (No tagea remoto automáticamente sin confirmación: respeta safety policy.)

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
RUN_DIR="runs/${RUN_ID}"
# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
log::set_dir "${RUN_DIR}/logs"
log::info "Fase G — release prep."

CHANGELOG_OUT="${RUN_DIR}/CHANGELOG.md"
LATEST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo '')"
RANGE="${LATEST_TAG:+${LATEST_TAG}..HEAD}"

{
  echo "# CHANGELOG — RUN ${RUN_ID}"
  echo
  if [[ -n "${RANGE}" ]]; then
    echo "Cambios desde \`${LATEST_TAG}\`:"
  else
    echo "Cambios desde el inicio del repo:"
  fi
  echo
  git log ${RANGE} --pretty=format:'- %s (%h)' || true
} > "${CHANGELOG_OUT}"
log::ok "CHANGELOG generado: ${CHANGELOG_OUT}"

# Sugerencia de SemVer (sin tagear automáticamente)
PREV="${LATEST_TAG:-v0.0.0}"
PREV_NO_V="${PREV#v}"
IFS=. read -r MA MI PA <<< "${PREV_NO_V}"
# Heurística: cualquier feat() → minor, cualquier fix() → patch
if git log ${RANGE} --pretty=format:'%s' 2>/dev/null | grep -qiE '^(feat|feature)(\(|:|!)'; then
  NEXT="v${MA}.$((MI+1)).0"
elif git log ${RANGE} --pretty=format:'%s' 2>/dev/null | grep -qiE '^fix(\(|:|!)'; then
  NEXT="v${MA}.${MI}.$((PA+1))"
else
  NEXT="v${MA}.${MI}.$((PA+1))"
fi
log::info "SemVer sugerido: ${NEXT} (anterior: ${PREV})"

cat > "${RUN_DIR}/release_notes.md" <<EOF
# Release ${NEXT}
**Run:** ${RUN_ID}
**Anterior tag:** ${PREV}

## Pasos manuales para liberar
1. Revisa \`${CHANGELOG_OUT}\` y \`runs/${RUN_ID}/_summary.md\`.
2. Asegúrate de que \`runs/${RUN_ID}/agents/_global/verifier.json\` diga \`APPROVE\`.
3. Tag firmado:
   \`\`\`bash
   git tag -s ${NEXT} -m "Release ${NEXT}"
   git push origin ${NEXT}
   \`\`\`
4. Render auto-deploya el push si \`autoDeploy: true\`.
5. Smoke test contra preview, luego merge a main si aplica.
EOF

cat > "${RUN_DIR}/_summary.md" <<EOF
# Resumen — RUN ${RUN_ID}

- Sprints orquestados: $(ls orchestration/sprints/ 2>/dev/null | wc -l)
- Reporte transversal: \`runs/${RUN_ID}/crosscut.report.json\`
- Dashboard: \`runs/${RUN_ID}/dashboard/index.html\`
- Release notes: \`runs/${RUN_ID}/release_notes.md\`
- Bloqueos abiertos: \`runs/${RUN_ID}/blockers.md\` (si existe)

## Próximos pasos
1. Revisar verifier.json de cada sprint.
2. Resolver requires_human_review pendientes.
3. Aprobar release ${NEXT} y push de tag manual.
EOF

cat > "${RUN_DIR}/state.json" <<EOF
{ "run_id": "${RUN_ID}", "phase_completed": "G", "next_phase": "DONE",
  "release_suggested": "${NEXT}",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)" }
EOF
log::ok "Fase G completada. Pipeline DONE."

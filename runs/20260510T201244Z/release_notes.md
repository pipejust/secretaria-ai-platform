# Release v0.0.1
**Run:** 20260510T201244Z
**Anterior tag:** v0.0.0

## Pasos manuales para liberar
1. Revisa `runs/20260510T201244Z/CHANGELOG.md` y `runs/20260510T201244Z/_summary.md`.
2. Asegúrate de que `runs/20260510T201244Z/agents/_global/verifier.json` diga `APPROVE`.
3. Tag firmado:
   ```bash
   git tag -s v0.0.1 -m "Release v0.0.1"
   git push origin v0.0.1
   ```
4. Render auto-deploya el push si `autoDeploy: true`.
5. Smoke test contra preview, luego merge a main si aplica.

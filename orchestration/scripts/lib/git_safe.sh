# shellcheck shell=bash
# Operaciones git defensivas para worktrees por sprint.

git_safe::ensure_clean() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    log::error "Working tree no está limpio. Aborta antes de crear worktree."
    return 1
  fi
}

git_safe::create_sprint_worktree() {
  local sprint_id="$1"
  local branch="auto/sprint-${sprint_id}"
  local wt_dir=".worktrees/sprint-${sprint_id}"
  mkdir -p .worktrees
  if [[ -d "${wt_dir}" ]]; then
    log::warn "Worktree ${wt_dir} ya existe. Reusando."
    return 0
  fi
  git fetch origin main >/dev/null 2>&1 || true
  git worktree add -b "${branch}" "${wt_dir}" origin/main 2>/dev/null \
    || git worktree add -b "${branch}" "${wt_dir}" main
  log::ok "Worktree creado: ${wt_dir} (rama ${branch})"
}

git_safe::discard_sprint_worktree() {
  local sprint_id="$1"
  local wt_dir=".worktrees/sprint-${sprint_id}"
  local branch="auto/sprint-${sprint_id}"
  log::warn "Descartando worktree ${wt_dir} y rama ${branch}"
  git worktree remove --force "${wt_dir}" 2>/dev/null || true
  git branch -D "${branch}" 2>/dev/null || true
}

git_safe::open_pr() {
  local sprint_id="$1"
  local title="$2"
  local body_file="$3"
  if ! command -v gh >/dev/null 2>&1; then
    log::warn "gh CLI no instalado; PR debe abrirse manualmente."
    return 0
  fi
  ( cd ".worktrees/sprint-${sprint_id}" && \
    git push -u origin "auto/sprint-${sprint_id}" && \
    gh pr create --title "${title}" --body-file "${body_file}" --base main
  )
}

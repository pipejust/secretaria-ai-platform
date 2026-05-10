# shellcheck shell=bash
# Logging estandarizado. Cada llamada va a stdout y a un archivo bajo runs/.

LOG_DIR=""

log::set_dir() { LOG_DIR="$1"; mkdir -p "$LOG_DIR"; }

log::_emit() {
  local level="$1"; shift
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local line="[${ts}] [${level}] $*"
  echo "$line"
  if [[ -n "${LOG_DIR}" ]]; then
    echo "$line" >> "${LOG_DIR}/orchestrator.log"
  fi
}

log::info()  { log::_emit "INFO"  "$@"; }
log::warn()  { log::_emit "WARN"  "$@"; }
log::error() { log::_emit "ERROR" "$@"; }
log::ok()    { log::_emit "OK"    "$@"; }

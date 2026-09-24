#!/usr/bin/env bash
# Guarda TU clave personal de Altum en el Llavero de macOS, la deja lista en ESTA terminal y en las
# siguientes. La clave la generas en Altum: Mi perfil → Mis datos → "Tu clave personal de API" → Regenerar.
# Es UNA sola para todos tus proyectos y este comando se corre UNA vez (o cuando la regeneres).
#
#   source scripts/sn/sn-clave-altum.sh              → la guarda y la exporta aquí mismo (recomendado)
#   bash   scripts/sn/sn-clave-altum.sh              → la guarda; la terminal actual necesita reabrirse
#   … sn-clave-altum.sh <empresa>                    → SN_ALTUM_KEY_<EMPRESA>, solo si trabajas con
#                                                      varias empresas que tienen su propio Altum
# El plugin también lee el Llavero por su cuenta, así que funciona aunque la variable no esté.

# Sin "set -e": si esto se ejecuta con "source", un fallo no debe cerrar la terminal de la persona.
sn_clave_altum() {
  if [ "$(uname)" != "Darwin" ]; then
    case "$(uname -s)" in
      MINGW*|MSYS*|CYGWIN*)
        echo "Estás en Windows: usa el asistente de Windows, que guarda la clave cifrada para tu usuario."
        echo "  powershell -ExecutionPolicy Bypass -File scripts\\sn\\sn-clave-altum.ps1"
        ;;
      *)
        echo "Este asistente usa el Llavero de macOS (en Windows: sn-clave-altum.ps1)."
        echo "En Linux, define la variable SN_ALTUM_KEY en tu terminal o pide ayuda al líder técnico."
        ;;
    esac
    return 1
  fi

  local empresa_raw="${1:-}" sufijo="" de="" var line
  if [ -n "$empresa_raw" ]; then
    sufijo="_$(printf '%s' "$empresa_raw" | tr '[:lower:]- ' '[:upper:]__' | tr -cd 'A-Z0-9_')"
    de=" para ${empresa_raw}"
  fi
  var="SN_ALTUM_KEY${sufijo}"

  echo "Pega tu clave personal de Altum${de} y presiona Enter (no se verá en pantalla):"
  security add-generic-password -U -a "$USER" -s "$var" -w || { echo "No se guardó la clave."; return 1; }

  # 1) En las terminales futuras: una línea en ~/.zshrc que la lee del Llavero (la clave no queda en el archivo).
  line="export ${var}=\"\$(security find-generic-password -a \"\$USER\" -s ${var} -w 2>/dev/null)\""
  touch ~/.zshrc
  grep -qF "$line" ~/.zshrc || printf '\n# Clave personal de Altum%s, guardada en el Llavero\n%s\n' "$de" "$line" >> ~/.zshrc

  # 2) En ESTA terminal, si el comando se corrió con "source".
  export "${var}=$(security find-generic-password -a "$USER" -s "$var" -w 2>/dev/null)"

  echo "Listo: ${var} quedó en el Llavero, exportada en esta terminal y en ~/.zshrc para las siguientes."
  if [ -z "${SN_CLAVE_SOURCED:-}" ]; then
    echo "Nota: como lo corriste con \"bash\", esta terminal no hereda la variable."
    echo "      Si la necesitas aquí mismo: source \"$0\"${empresa_raw:+ $empresa_raw}"
    echo "      (Claude Code no la necesita: el plugin lee el Llavero solo.)"
  fi
  echo "Comprueba con: \"¿quién soy en Altum?\""
}

# ¿Se ejecutó con "source"? En ese caso la variable sí queda en la terminal de la persona.
if [ -n "${ZSH_EVAL_CONTEXT:-}" ]; then
  case "$ZSH_EVAL_CONTEXT" in *:file) SN_CLAVE_SOURCED=1 ;; esac
elif [ -n "${BASH_SOURCE:-}" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
  SN_CLAVE_SOURCED=1
fi

sn_clave_altum "$@"

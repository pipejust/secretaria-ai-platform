"""I18n helpers para el pipeline de IA y los resúmenes que recibimos
de Fireflies.

Cubre dos casos:

1. Section headers del resumen mega-summary que prependen las apps
   Fireflies (Resumen General, Puntos Clave, Notas, …). Antes estaban
   hardcoded en castellano, así que los tenants en catalán/inglés veían
   acta con "### Resumen General" arriba aunque el resto del contenido
   estuviera traducido. Ahora resolvemos por tenant language.

2. Nombre humano del idioma para meter en system prompts del LLM
   ("ESPAÑOL", "CATALÁN", "INGLÉS").

Uso típico:

    from services.i18n_pipeline import section_headers, lang_label

    h = section_headers(tenant.default_language)
    text = f"### {h['general_summary']}\n{overview}"

Nunca lanzar excepciones por idioma desconocido: caer a "es" por
defecto para no romper el pipeline ante un valor inesperado.
"""

from __future__ import annotations

from typing import Dict, Optional

_SUPPORTED = {"es", "ca", "en"}


def _normalize(lang: Optional[str]) -> str:
    """Normaliza un string de idioma al subset soportado. Caer a 'es' si
    viene None, vacío, o algo que no esté en el set. Sin advertencias:
    queremos pipeline robusto, no logs ruidosos."""
    if not lang:
        return "es"
    key = lang.strip().lower()[:2]
    return key if key in _SUPPORTED else "es"


# --- Headers de secciones del mega-summary ---
# Estos son los títulos `### Xxxx` que aparecen al inicio de cada bloque
# del resumen estructurado. Mantenelos cortos para que no rompan layouts.
_SECTION_HEADERS: Dict[str, Dict[str, str]] = {
    "es": {
        "general_summary": "Resumen General",
        "key_points": "Puntos Clave",
        "notes": "Notas",
        "understood_notes": "Notas Entendidas",
        "executive_summary": "Resumen Ejecutivo",
    },
    "ca": {
        "general_summary": "Resum General",
        "key_points": "Punts Clau",
        "notes": "Notes",
        "understood_notes": "Notes Enteses",
        "executive_summary": "Resum Executiu",
    },
    "en": {
        "general_summary": "General Summary",
        "key_points": "Key Points",
        "notes": "Notes",
        "understood_notes": "Captured Notes",
        "executive_summary": "Executive Summary",
    },
}


def section_headers(lang: Optional[str]) -> Dict[str, str]:
    """Devuelve el diccionario completo de section headers para el idioma
    dado. Acceso por slug: `section_headers('ca')['general_summary']`."""
    return _SECTION_HEADERS[_normalize(lang)]


# --- Nombres legibles del idioma para system prompts del LLM ---
# El LLM responde mejor cuando lo instruimos en MAYÚSCULAS y en su propio
# idioma: "Responde EN ESPAÑOL" funciona mejor que "Respond in Spanish".
_LANG_LABELS: Dict[str, str] = {
    "es": "ESPAÑOL",
    "ca": "CATALÁN",
    "en": "INGLÉS",
}


def lang_label(lang: Optional[str]) -> str:
    """Etiqueta humana para meter en system prompts del LLM."""
    return _LANG_LABELS[_normalize(lang)]

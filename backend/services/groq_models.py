"""Qué modelo de Groq usa cada cosa. **Un solo sitio.**

Groq apagó todos los modelos de chat Llama el 16 de agosto de 2026. Los
identificadores estaban repetidos en cinco ficheros, así que el apagado
rompió el pipeline en cinco puntos y arreglarlo obligaba a encontrarlos
todos. Aquí no: la próxima vez es una línea.

Comprobado contra la cuenta el 18-ago-2026 —`GET /v1/models` devuelve 13
y ninguno es Llama de chat— y probado en modo JSON, que es lo que pide
todo el pipeline:

    gpt-oss-120b   0.7 s   json ok
    gpt-oss-20b    0.4 s   json ok
    qwen3.6-27b    3.3 s   json ok

Whisper **no está afectado**: `whisper-large-v3` y `-turbo` siguen vivos,
así que la transcripción no se tocó.
"""

from __future__ import annotations

import os

# Razonamiento largo: actas, decisiones, riesgos, respuestas del Ask.
# Sustituye a llama-3.3-70b-versatile.
MODELO_PRINCIPAL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Tareas cortas y frecuentes: entender la pregunta antes de buscar.
# Sustituye a llama-3.1-8b-instant.
MODELO_RAPIDO = os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b")

# Alternativa si el principal falla o se queda sin cuota.
MODELO_ALTERNATIVO = os.getenv("GROQ_MODEL_FALLBACK", "qwen/qwen3.6-27b")

# Transcripción. Intacto tras la deprecación.
MODELO_VOZ = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

# Se leen del entorno a propósito: si Groq vuelve a apagar algo, se cambia
# una variable en el despliegue y el servicio sigue, sin esperar a un
# despliegue de código.

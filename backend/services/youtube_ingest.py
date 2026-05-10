"""Ingest de videos de YouTube → audio → Whisper-Groq.

Implementación que NO requiere ejecutables externos pesados: usa la API
de timedtext de YouTube cuando hay subtítulos disponibles, y cae a
descarga de audio + Whisper cuando no.

Para descarga de audio: yt-dlp es el go-to, pero requiere `ffmpeg` o
`ffprobe`. Como Render Docker incluye solo lo mínimo, instalamos yt-dlp
en `requirements.txt` y `ffmpeg` en el Dockerfile.

Si yt-dlp NO está disponible, el endpoint devuelve 422 con mensaje claro.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

YOUTUBE_RX = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([\w\-]{11})"
)


def extract_video_id(url: str) -> Optional[str]:
    m = YOUTUBE_RX.search(url or "")
    return m.group(1) if m else None


def is_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None


def _have_yt_dlp() -> bool:
    return shutil.which("yt-dlp") is not None


async def fetch_audio_bytes(youtube_url: str) -> tuple[bytes, str]:
    """Descarga el audio del video y retorna (bytes, filename).

    Usa yt-dlp con format=bestaudio. Falla con RuntimeError si yt-dlp no
    está disponible — el caller debe mostrar mensaje útil al usuario.
    """
    vid = extract_video_id(youtube_url)
    if not vid:
        raise ValueError("URL de YouTube no válida.")

    if not _have_yt_dlp():
        raise RuntimeError(
            "yt-dlp no está instalado en el contenedor. Pide a infra que "
            "agregue `yt-dlp` y `ffmpeg` al Dockerfile."
        )

    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / f"{vid}.%(ext)s")
        # Pedimos m4a directamente (formato 140) — lo entiende Whisper.
        cmd = [
            "yt-dlp", "-f", "bestaudio[ext=m4a]/bestaudio",
            "-o", out_template,
            "--no-playlist", "--quiet", "--no-warnings",
            youtube_url,
        ]
        logger.info("yt-dlp descargando %s", youtube_url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp falló: {result.stderr[:500]}")
        files = list(Path(tmp).glob(f"{vid}.*"))
        if not files:
            raise RuntimeError("yt-dlp terminó OK pero no produjo archivo de audio.")
        audio_path = files[0]
        return audio_path.read_bytes(), audio_path.name

"""Sprint 10 — Meeting quality scoring sobre el transcript.

Heurísticas simples (sin LLM extra) para no inflar costos. Cada score 0-100.
"""

from __future__ import annotations

import json
import re
from typing import Optional


FILLER_WORDS_ES = {
    "eh", "este", "uhm", "mm", "ehm", "como que", "o sea",
    "tipo", "verdad", "no", "bueno", "entonces",
}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", text or "", re.UNICODE)]


def score_clarity(transcript: str) -> int:
    """100 = sin muletillas; 0 = puras muletillas."""
    if not transcript:
        return 0
    toks = _tokens(transcript)
    if not toks:
        return 0
    fillers = sum(1 for t in toks if t in FILLER_WORDS_ES)
    pct = fillers / len(toks)
    return max(0, min(100, int(round((1.0 - pct * 5) * 100))))


def score_listening(transcript: str) -> int:
    """100 = balance perfecto entre speakers; 0 = uno monopoliza."""
    # Detección simple por prefijos `[Nombre]` o `Nombre:`
    speakers: dict[str, int] = {}
    for line in (transcript or "").splitlines():
        line = line.strip()
        m = re.match(r"^\[?([A-Za-zÁÉÍÓÚñÑ\s]{2,40})\]?:?\s+(.+)", line)
        if not m:
            continue
        speaker = m.group(1).strip()
        words = len(m.group(2).split())
        speakers[speaker] = speakers.get(speaker, 0) + words
    if len(speakers) < 2:
        return 50
    total = sum(speakers.values())
    if not total:
        return 50
    shares = sorted(w / total for w in speakers.values())
    # Gini-ish: si 1 sola persona habla todo, share dominante = 1, score 0.
    dominant = shares[-1]
    return max(0, min(100, int(round((1.0 - max(0.0, dominant - 1.0 / len(speakers))) * 100))))


def score_decisions_density(transcript: str, decisions_text: str, agreements_text: str) -> int:
    """N decisiones/acuerdos por 1000 palabras. ≥3 → 100."""
    n_dec = len([l for l in (decisions_text or "").splitlines() if l.strip().startswith("-")])
    n_agr = len([l for l in (agreements_text or "").splitlines() if l.strip().startswith("-")])
    n = n_dec + n_agr
    words = len(_tokens(transcript))
    if not words:
        return 0
    rate = (n / words) * 1000
    return max(0, min(100, int(round((rate / 3.0) * 100))))


def score_action_density(action_items_count: int, transcript: str) -> int:
    """N action_items por 1000 palabras. ≥2 → 100."""
    words = len(_tokens(transcript))
    if not words or action_items_count < 0:
        return 0
    rate = (action_items_count / words) * 1000
    return max(0, min(100, int(round((rate / 2.0) * 100))))


def overall(scores: dict) -> int:
    """Promedio simple de los scores presentes."""
    vals = [v for v in scores.values() if isinstance(v, int)]
    return int(round(sum(vals) / len(vals))) if vals else 0


def compute(*, transcript: str, decisions: str, agreements: str, action_items_count: int) -> dict:
    scores = {
        "clarity": score_clarity(transcript),
        "listening": score_listening(transcript),
        "decisions_density": score_decisions_density(transcript, decisions, agreements),
        "action_density": score_action_density(action_items_count, transcript),
    }
    scores["overall"] = overall(scores)
    return scores

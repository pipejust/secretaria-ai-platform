"""Una misma reunión grabada por el bot propio y por Fireflies: gana el bot.

Cuando una empresa acepta las dos fuentes y los dos bots entran a la misma
reunión, llegan dos sesiones. Sin esto cada una generaba sus tareas, sus
correos y sus envíos. Aquí se detecta la pareja y la de Fireflies queda
archivada como duplicada: se conserva, pero no se analiza ni avisa a nadie.

La detección compara las transcripciones. Son dos motores distintos oyendo
el mismo audio, así que no coinciden palabra por palabra, pero comparten la
mayoría de sus tríos de palabras: en la reunión real con la que se calibró,
un 73 %, frente a menos de un 4 % entre reuniones distintas del mismo
equipo el mismo día.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from models import ActionItem, MeetingSession

logger = logging.getLogger(__name__)

# Tríos de palabras compartidos, sobre la transcripción más corta de las dos.
UMBRAL_PARECIDO = 0.30
# Menos que esto es un saludo o una prueba de micrófono: coincide con cualquier cosa.
MIN_TRIOS = 40
# Los dos bots entran casi a la vez, pero uno puede llegar tarde.
VENTANA_INICIO = timedelta(minutes=90)
# Solo se mira lo reciente: una reunión no reaparece días después.
VENTANA_BUSQUEDA = timedelta(days=2)
ARCHIVADA = "archived"
# Estados en los que nadie ha trabajado todavía sobre la sesión.
SIN_TOCAR = {"pending", "processing"}


def es_del_bot(s: MeetingSession) -> bool:
    return (s.fireflies_id or "").startswith("BOT-")


def es_de_fireflies(s: MeetingSession) -> bool:
    ident = s.fireflies_id or ""
    return bool(ident) and not ident.startswith(("BOT-", "MANUAL-", "manual_"))


def _trios(texto: str) -> set[str]:
    sin_hablante = re.sub(r"^\[[^\]]*\]\s*", "", texto or "", flags=re.M)
    plano = unicodedata.normalize("NFD", sin_hablante.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    palabras = re.findall(r"[a-z0-9]+", plano)
    return {" ".join(palabras[i:i + 3]) for i in range(len(palabras) - 2)}


def parecido(a: str, b: str) -> float:
    """Parte de la transcripción más corta que también está en la otra (0–1)."""
    ta, tb = _trios(a), _trios(b)
    menor = min(len(ta), len(tb))
    return len(ta & tb) / menor if menor >= MIN_TRIOS else 0.0


def _inicio(s: MeetingSession) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat((s.date or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def gemela(db: Session, sesion: MeetingSession) -> Optional[MeetingSession]:
    """La sesión de la OTRA fuente que es la misma reunión que `sesion`, si existe."""
    if es_del_bot(sesion):
        de_la_otra = es_de_fireflies
    elif es_de_fireflies(sesion):
        de_la_otra = es_del_bot
    else:
        return None
    inicio = _inicio(sesion)
    if inicio is None or not (sesion.raw_transcript or "").strip():
        return None
    desde = (datetime.now() - VENTANA_BUSQUEDA).isoformat()
    candidatas = db.exec(
        select(MeetingSession).where(
            MeetingSession.tenant_id == sesion.tenant_id,
            MeetingSession.id != sesion.id,
            MeetingSession.status != ARCHIVADA,
            MeetingSession.created_at >= desde,
        )
    ).all()
    mejor, mejor_valor = None, UMBRAL_PARECIDO
    for otra in candidatas:
        su_inicio = _inicio(otra)
        if not de_la_otra(otra) or su_inicio is None or abs(su_inicio - inicio) > VENTANA_INICIO:
            continue
        valor = parecido(sesion.raw_transcript, otra.raw_transcript or "")
        if valor >= mejor_valor:
            mejor, mejor_valor = otra, valor
    if mejor:
        logger.info(
            "Sesión %s y sesión %s son la misma reunión (parecido %.2f).",
            sesion.id, mejor.id, mejor_valor,
        )
    return mejor


def archivar_duplicada(db: Session, perdedora: MeetingSession, ganadora: MeetingSession) -> None:
    """Deja `perdedora` archivada y apuntando a `ganadora`; sus tareas abiertas se cancelan."""
    perdedora.status = ARCHIVADA
    perdedora.duplicate_of = ganadora.id
    db.add(perdedora)
    # ponytail: cancelación directa, sin pasar por el historial de tareas; si
    # alguien necesita auditar estas cancelaciones, emitir aquí los eventos.
    abiertas = db.exec(
        select(ActionItem).where(
            ActionItem.session_id == perdedora.id,
            ActionItem.status.in_(("pending", "blocked")),
        )
    ).all()
    for tarea in abiertas:
        tarea.status = "cancelled"
        db.add(tarea)
    db.commit()
    logger.info(
        "Sesión %s archivada como duplicada de la %s (%s tareas canceladas).",
        perdedora.id, ganadora.id, len(abiertas),
    )


def resolver_al_llegar(db: Session, sesion: MeetingSession) -> bool:
    """Se llama antes de analizar una sesión recién llegada. True = no la analices.

    * Llega la de Fireflies y ya está la del bot → se archiva la de Fireflies.
    * Llega la del bot y ya está la de Fireflies → se archiva la de Fireflies,
      salvo que alguien ya la haya trabajado: entonces se dejan las dos y se
      avisa en el registro, porque borrar una curación hecha es peor que un
      duplicado.
    """
    otra = gemela(db, sesion)
    if otra is None:
        return False
    if es_de_fireflies(sesion):
        archivar_duplicada(db, sesion, otra)
        return True
    if otra.status in SIN_TOCAR:
        archivar_duplicada(db, otra, sesion)
    else:
        logger.warning(
            "Sesión %s (bot) duplica la %s (Fireflies), que ya está en estado «%s»: "
            "se dejan las dos.", sesion.id, otra.id, otra.status,
        )
    return False

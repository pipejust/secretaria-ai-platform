"""Sprint 10 — Analytics + dashboard ROI ejecutivo + reuniones recurrentes."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, User
from routers.auth import get_current_user
from services import quality_scoring

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["Analytics (Sprint 10)"])


@router.get("/sessions/{session_id}/quality")
def session_quality(
    session_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    sess = db.get(MeetingSession, session_id)
    if not sess:
        return {"error": "session not found"}
    actions = db.exec(
        select(ActionItem).where(ActionItem.session_id == session_id)
    ).all()
    scores = quality_scoring.compute(
        transcript=sess.raw_transcript or "",
        decisions=sess.processed_decisions or "",
        agreements=sess.processed_agreements or "",
        action_items_count=len(actions),
    )
    return {"session_id": session_id, "scores": scores}


@router.get("/roi")
def roi_dashboard(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Dashboard ROI ejecutivo del último N días."""
    sessions = db.exec(select(MeetingSession)).all()
    actions = db.exec(select(ActionItem)).all()

    # Estimar palabras como proxy de minutos: 150 palabras ≈ 1 min
    total_minutes = 0
    for s in sessions:
        words = len((s.raw_transcript or "").split())
        total_minutes += words // 150

    by_owner: Counter = Counter()
    for a in actions:
        owner = (a.owner_name or "Sin asignar").strip() or "Sin asignar"
        by_owner[owner] += 1
    top_owners = [{"owner": o, "count": c} for o, c in by_owner.most_common(10)]

    auto = sum(1 for s in sessions if s.status == "processed")
    total = len(sessions) or 1
    completed = sum(1 for a in actions if a.status == "done")
    pending = sum(1 for a in actions if a.status == "pending")

    return {
        "window_days": days,
        "sessions_count": len(sessions),
        "estimated_meeting_minutes": total_minutes,
        "estimated_meeting_hours": round(total_minutes / 60, 1),
        "sessions_auto_dispatched_pct": round(auto / total * 100, 1),
        "action_items_total": len(actions),
        "action_items_completed": completed,
        "action_items_pending": pending,
        "completion_rate_pct": round((completed / max(len(actions), 1)) * 100, 1),
        "top_owners": top_owners,
    }


def _normalize_title(t: str) -> str:
    """Quita artículos, números de iteración y normaliza espacios."""
    t = re.sub(r"\b(weekly|daily|sync|kickoff|reunión|reunion|meeting|standup)\b", "", (t or "").lower())
    t = re.sub(r"[#\-_]+\d+", "", t)
    t = re.sub(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@router.get("/recurring")
def recurring_meetings(
    similarity_threshold: float = Query(0.8, ge=0.5, le=1.0),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Detecta series recurrentes por similitud fuzzy de títulos."""
    sessions = db.exec(
        select(MeetingSession).order_by(MeetingSession.id.desc())
    ).all()

    # Agrupamiento simple O(n²). Para >1000 sesiones convendría LSH.
    series: list[dict] = []
    used = set()
    for i, s in enumerate(sessions):
        if i in used:
            continue
        norm_i = _normalize_title(s.title)
        if not norm_i:
            continue
        members = [s]
        used.add(i)
        for j in range(i + 1, len(sessions)):
            if j in used:
                continue
            norm_j = _normalize_title(sessions[j].title)
            if not norm_j:
                continue
            ratio = SequenceMatcher(None, norm_i, norm_j).ratio()
            if ratio >= similarity_threshold:
                members.append(sessions[j])
                used.add(j)
        if len(members) >= 2:
            series.append({
                "title_pattern": norm_i,
                "occurrences": len(members),
                "session_ids": [m.id for m in members],
                "latest_date": max((m.date or "") for m in members),
            })

    series.sort(key=lambda x: -x["occurrences"])
    return {"series_detected": len(series), "series": series[:50]}

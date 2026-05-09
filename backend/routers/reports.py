"""Reportes ejecutivos por período (semanal / mensual / personalizado).

Endpoints:
  GET /api/reports/data         → JSON con métricas + listados
  GET /api/reports/pdf          → mismo contenido, renderizado a PDF (fpdf2)
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["Reportes"])


def _resolve_window(period: str, ref: Optional[str]) -> tuple[datetime, datetime, str]:
    """Devuelve (inicio, fin, etiqueta_humana) para un período `week` o `month`."""
    base = datetime.now()
    if ref:
        try:
            base = datetime.fromisoformat(ref)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parámetro `ref` no es ISO 8601 válido")

    if period == "week":
        start = base - timedelta(days=base.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7) - timedelta(microseconds=1)
        label = f"Semana del {start.strftime('%d/%m/%Y')} al {(start + timedelta(days=6)).strftime('%d/%m/%Y')}"
    elif period == "month":
        start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Final = primer día del mes siguiente − 1 microseg
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(microseconds=1)
        label = start.strftime("%B %Y").capitalize()
    else:
        raise HTTPException(status_code=400, detail="period debe ser 'week' o 'month'")
    return start, end, label


def _date_in_window(value, start: datetime, end: datetime) -> bool:
    if not value:
        return False
    s = str(value).strip()
    parsed: Optional[datetime] = None
    # Epoch ms (lo que mandamos desde el frontend)
    if s.isdigit():
        try:
            parsed = datetime.fromtimestamp(int(s) / 1000)
        except (ValueError, OSError):
            parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                parsed = None
    if parsed is None:
        return False
    if parsed.tzinfo:
        parsed = parsed.replace(tzinfo=None)
    return start <= parsed <= end


def _build_report(
    db: Session,
    period: str,
    ref: Optional[str],
    project_id: Optional[int],
) -> Dict[str, Any]:
    start, end, label = _resolve_window(period, ref)

    sessions_query = select(MeetingSession)
    if project_id is not None:
        sessions_query = sessions_query.where(MeetingSession.project_id == project_id)
    all_sessions = db.exec(sessions_query).all()
    sessions_in_window = [s for s in all_sessions if _date_in_window(s.date, start, end)]

    project_names = {p.id: p.name for p in db.exec(select(Project)).all() if p.id}

    # Action items vinculados a esas sesiones
    session_ids = [s.id for s in sessions_in_window if s.id is not None]
    items: list[ActionItem] = []
    if session_ids:
        items = list(
            db.exec(select(ActionItem).where(ActionItem.session_id.in_(session_ids))).all()
        )

    completed_in_window = []
    for it in db.exec(select(ActionItem)).all():
        if it.completed_at and _date_in_window(it.completed_at, start, end):
            completed_in_window.append(it)
            if project_id is not None:
                # filtramos por proyecto a través de su sesión
                ms = db.get(MeetingSession, it.session_id)
                if ms is None or ms.project_id != project_id:
                    completed_in_window.pop()

    by_status = {"pending": 0, "blocked": 0, "done": 0, "cancelled": 0}
    for it in items:
        by_status[it.status] = by_status.get(it.status, 0) + 1

    # Top responsables del período
    by_owner: Dict[str, int] = {}
    for it in items:
        owner = (it.owner_name or "Sin asignar").strip() or "Sin asignar"
        by_owner[owner] = by_owner.get(owner, 0) + 1
    top_owners = sorted(by_owner.items(), key=lambda kv: -kv[1])[:10]

    return {
        "period": period,
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "project_id": project_id,
        "project_name": project_names.get(project_id) if project_id else "Todos los proyectos",
        "metrics": {
            "sessions_count": len(sessions_in_window),
            "action_items_total": len(items),
            "action_items_by_status": by_status,
            "completed_in_window": len(completed_in_window),
            "auto_dispatched": sum(1 for s in sessions_in_window if s.status == "processed"),
        },
        "sessions": [
            {
                "id": s.id, "title": s.title, "date": s.date,
                "status": s.status,
                "project_name": project_names.get(s.project_id) if s.project_id else "General",
            }
            for s in sorted(sessions_in_window, key=lambda x: str(x.date), reverse=True)
        ],
        "top_owners": [{"owner": o, "count": c} for o, c in top_owners],
    }


@router.get("/data")
def get_report_data(
    db: Session = Depends(get_session),
    period: str = Query("week", description="'week' o 'month'"),
    ref: Optional[str] = Query(None, description="Fecha ISO de referencia. Default: hoy."),
    project_id: Optional[int] = Query(None),
):
    return _build_report(db, period, ref, project_id)


@router.get("/pdf")
def get_report_pdf(
    db: Session = Depends(get_session),
    period: str = Query("week"),
    ref: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
):
    """Renderiza el reporte a PDF usando fpdf2 (sin dependencias externas pesadas)."""
    from fpdf import FPDF

    report = _build_report(db, period, ref, project_id)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # --- Header ---
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Reporte ejecutivo Notiva", ln=1)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, report["label"], ln=1)
    pdf.cell(0, 6, f"Proyecto: {report['project_name']}", ln=1)
    pdf.cell(0, 6, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=1)
    pdf.ln(4)

    # --- Métricas ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "Métricas del período", ln=1)
    pdf.set_font("Helvetica", "", 11)
    m = report["metrics"]
    rows = [
        ("Reuniones procesadas", str(m["sessions_count"])),
        ("Tareas totales", str(m["action_items_total"])),
        ("  · Pendientes", str(m["action_items_by_status"].get("pending", 0))),
        ("  · Bloqueadas", str(m["action_items_by_status"].get("blocked", 0))),
        ("  · Completadas", str(m["action_items_by_status"].get("done", 0))),
        ("  · Canceladas", str(m["action_items_by_status"].get("cancelled", 0))),
        ("Tareas completadas en la ventana", str(m["completed_in_window"])),
        ("Sesiones auto-despachadas", str(m["auto_dispatched"])),
    ]
    for label, value in rows:
        pdf.cell(110, 6, label, border=0)
        pdf.cell(0, 6, value, border=0, ln=1)
    pdf.ln(4)

    # --- Reuniones del período ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "Reuniones del período", ln=1)
    pdf.set_font("Helvetica", "", 10)
    if not report["sessions"]:
        pdf.cell(0, 6, "Sin reuniones en este período.", ln=1)
    else:
        for s in report["sessions"][:30]:
            title = (s["title"] or "Sin título")[:80]
            extra = f"  ·  {s['project_name']}  ·  {s['status']}"
            pdf.multi_cell(0, 5, f"- {title}{extra}")
    pdf.ln(2)

    # --- Top responsables ---
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "Responsables con más tareas", ln=1)
    pdf.set_font("Helvetica", "", 10)
    if not report["top_owners"]:
        pdf.cell(0, 6, "Sin responsables asignados.", ln=1)
    else:
        for o in report["top_owners"]:
            pdf.cell(0, 6, f"{o['owner']:<40s}  {o['count']} tareas", ln=1)

    buffer = io.BytesIO()
    raw = pdf.output(dest="S")
    if isinstance(raw, str):
        buffer.write(raw.encode("latin-1"))
    else:
        buffer.write(bytes(raw))
    buffer.seek(0)

    safe_label = report["label"].replace("/", "-").replace(" ", "_")
    filename = f"Reporte_Notiva_{safe_label}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

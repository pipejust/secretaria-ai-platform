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


def _resolve_window(
    period: str,
    ref: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[datetime, datetime, str]:
    """Devuelve (inicio, fin, etiqueta_humana) según `period`.

    Soporta:
      - period='custom' + start_date/end_date → rango libre.
      - period='week'   → semana ISO de `ref`/hoy.
      - period='month'  → mes calendario de `ref`/hoy.
    """
    base = datetime.now()
    if ref:
        try:
            base = datetime.fromisoformat(ref)
        except ValueError:
            raise HTTPException(status_code=400, detail="Parámetro `ref` no es ISO 8601 válido")

    if period == "custom":
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="period='custom' requiere start_date y end_date")
        try:
            start = datetime.fromisoformat(start_date).replace(hour=0, minute=0, second=0, microsecond=0)
            end = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date/end_date deben ser ISO 8601")
        if end < start:
            raise HTTPException(status_code=400, detail="end_date debe ser >= start_date")
        label = f"Del {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
    elif period == "all":
        # Sin restricción de fecha — cubre toda la historia del tenant.
        start = datetime(2000, 1, 1, 0, 0, 0)
        end = datetime(2100, 12, 31, 23, 59, 59)
        label = "Siempre"
    elif period == "week":
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
        raise HTTPException(status_code=400, detail="period debe ser 'week', 'month', 'all' o 'custom'")
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


def _parse_due_date(value) -> Optional[datetime]:
    """Parser laxo igual al de pendientes.py para reuso aquí."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _classify_project_by_sessions(
    sessions: List[MeetingSession],
    now: datetime,
    stale_days: int = 14,
) -> str:
    """Deriva el estado del proyecto a partir del estado de CURACIÓN de sus
    sesiones (no por action_items). Esta es la métrica que tiene sentido para
    el dashboard de reportes según el flujo de la plataforma: una sesión
    'completed' significa que fue revisada/curada por el equipo.

    Reglas:
      - No hay sesiones (no archivadas) → not_started.
      - 100% sesiones completadas      → completed.
      - Hay completadas y pendientes   → in_progress.
      - Todas pendientes y al menos una con > stale_days días sin curar → overdue.
      - Todas pendientes recientes     → pending.
    """
    # Ignoramos archivadas; cuentan solo sesiones activas (pending/processing/completed).
    active = [s for s in sessions if s.status not in ("archived",)]
    if not active:
        return "not_started"

    completed = [s for s in active if s.status == "completed"]
    pending   = [s for s in active if s.status in ("pending", "processing")]

    # Todo curado.
    if completed and not pending:
        return "completed"

    # Mezcla: hay avance pero no terminado.
    if completed and pending:
        return "in_progress"

    # Solo pendientes — verificar si hay alguna estancada.
    if pending and not completed:
        for s in pending:
            d = _parse_due_date(s.date)
            if d and (now.replace(tzinfo=None) - d.replace(tzinfo=None)).days > stale_days:
                return "overdue"
        return "pending"

    return "not_started"


def _project_progress_pct_by_sessions(sessions: List[MeetingSession]) -> int:
    """Porcentaje de progreso = sesiones curadas / sesiones activas."""
    active = [s for s in sessions if s.status not in ("archived",)]
    if not active:
        return 0
    completed = sum(1 for s in active if s.status == "completed")
    return int(round((completed / len(active)) * 100))


def _project_progress_pct(items: List[ActionItem]) -> int:
    """Porcentaje de cumplimiento de tareas = action_items 'done' / total.

    Útil para tooltips/breakdown adicional, no para clasificar el proyecto.
    """
    if not items:
        return 0
    done = sum(1 for it in items if it.status == "done")
    return int(round((done / len(items)) * 100))


def _top_owner_for_project(items: List[ActionItem]) -> str:
    """Responsable principal = quien tiene más action_items asignados."""
    if not items:
        return "Sin asignar"
    by_owner: Dict[str, int] = {}
    for it in items:
        owner = (it.owner_name or "").strip() or "Sin asignar"
        by_owner[owner] = by_owner.get(owner, 0) + 1
    return sorted(by_owner.items(), key=lambda kv: -kv[1])[0][0]


def _build_decisions_timeline(
    sessions_in_window: List[MeetingSession],
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    """Cuenta sesiones por día. Cada sesión ≈ una reunión donde se tomaron
    decisiones; se acumulan a lo largo del período para mostrar tendencia.
    """
    days = max(1, (end.date() - start.date()).days + 1)
    # Para no saturar la línea cuando el rango es muy grande, agrupamos en
    # ~7 buckets como referencia visual del diseño.
    bucket_count = min(days, 7) if days > 1 else 1
    bucket_size = max(1, days // bucket_count)

    # Conteo por día.
    per_day: Dict[str, int] = {}
    for s in sessions_in_window:
        d = _parse_due_date(s.date)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        per_day[key] = per_day.get(key, 0) + 1

    points: List[Dict[str, Any]] = []
    cumulative = 0
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        bucket_end = current + timedelta(days=bucket_size) - timedelta(microseconds=1)
        if bucket_end > end:
            bucket_end = end
        count = 0
        d = current
        while d <= bucket_end:
            count += per_day.get(d.strftime("%Y-%m-%d"), 0)
            d += timedelta(days=1)
        cumulative += count
        points.append({
            "label": current.strftime("%-d %b").lower() if hasattr(current, "strftime") else "",
            "iso": current.strftime("%Y-%m-%d"),
            "value": cumulative,
        })
        current = bucket_end + timedelta(microseconds=1)
    return points


def _build_report(
    db: Session,
    period: str,
    ref: Optional[str],
    project_id: Optional[int],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    start, end, label = _resolve_window(period, ref, start_date, end_date)
    now = datetime.now()

    sessions_query = select(MeetingSession)
    if project_id is not None:
        sessions_query = sessions_query.where(MeetingSession.project_id == project_id)
    all_sessions = db.exec(sessions_query).all()
    sessions_in_window = [s for s in all_sessions if _date_in_window(s.date, start, end)]

    all_projects = list(db.exec(select(Project)).all())
    project_names = {p.id: p.name for p in all_projects if p.id}

    # Action items vinculados a esas sesiones.
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
                ms = db.get(MeetingSession, it.session_id)
                if ms is None or ms.project_id != project_id:
                    completed_in_window.pop()

    by_status = {"pending": 0, "blocked": 0, "done": 0, "cancelled": 0}
    for it in items:
        by_status[it.status] = by_status.get(it.status, 0) + 1

    # Top responsables del período.
    by_owner: Dict[str, int] = {}
    for it in items:
        owner = (it.owner_name or "Sin asignar").strip() or "Sin asignar"
        by_owner[owner] = by_owner.get(owner, 0) + 1
    top_owners = sorted(by_owner.items(), key=lambda kv: -kv[1])[:10]

    # ============================================================
    # Datos derivados — TODO filtrado por la ventana del periodo.
    # ============================================================

    # Set de proyectos con actividad EN LA VENTANA.
    active_in_window = {s.project_id for s in sessions_in_window if s.project_id}

    # Si el usuario eligió un proyecto específico, lo INCLUIMOS aunque no
    # tenga actividad en la ventana (para que vea cero/empty state real).
    # Si NO, solo incluimos proyectos que sí tuvieron movimiento.
    target_projects = [
        p for p in all_projects
        if p.is_active
        and ((project_id is None and p.id in active_in_window) or (project_id is not None and p.id == project_id))
    ]

    # Sesiones de cada proyecto SOLO dentro de la ventana.
    project_sessions: Dict[int, List[MeetingSession]] = {p.id: [] for p in target_projects if p.id}
    for s in sessions_in_window:
        if s.project_id in project_sessions:
            project_sessions[s.project_id].append(s)

    # Action items de cada proyecto SOLO derivados de sus sesiones en ventana.
    # `items` ya estaba filtrado a session_ids in window, así que mapeamos
    # cada item al project_id de su sesión.
    project_items: Dict[int, List[ActionItem]] = {p.id: [] for p in target_projects if p.id}
    sid_to_pid = {s.id: s.project_id for s in sessions_in_window if s.id is not None}
    for it in items:
        pid = sid_to_pid.get(it.session_id)
        if pid in project_items:
            project_items[pid].append(it)

    projects_breakdown_counts = {
        "completed": 0, "in_progress": 0, "pending": 0, "overdue": 0, "not_started": 0,
    }
    top_projects: List[Dict[str, Any]] = []
    for p in target_projects:
        if not p.id:
            continue
        its = project_items.get(p.id, [])
        proj_sessions = project_sessions.get(p.id, [])  # ya filtrado a la ventana
        # CLASIFICACIÓN: por curación de sesiones DE LA VENTANA, no del proyecto entero.
        status_key = _classify_project_by_sessions(proj_sessions, now)
        projects_breakdown_counts[status_key] = projects_breakdown_counts.get(status_key, 0) + 1

        items_break = {"pending": 0, "done": 0, "blocked": 0, "cancelled": 0, "overdue": 0}
        for it in its:
            items_break[it.status] = items_break.get(it.status, 0) + 1
            if it.status in ("pending", "blocked"):
                d = _parse_due_date(it.due_date)
                if d and d.replace(tzinfo=None) < now.replace(tzinfo=None):
                    items_break["overdue"] += 1

        active_sessions = [s for s in proj_sessions if s.status not in ("archived",)]
        sessions_break = {
            "completed": sum(1 for s in active_sessions if s.status == "completed"),
            "pending":   sum(1 for s in active_sessions if s.status in ("pending", "processing")),
            "total":     len(active_sessions),
        }

        top_projects.append({
            "id": p.id,
            "name": p.name,
            "owner": _top_owner_for_project(its),
            # progress refleja curación de sesiones EN LA VENTANA.
            "progress": _project_progress_pct_by_sessions(proj_sessions),
            "tasks_progress": _project_progress_pct(its),
            "status": status_key,
            "items_breakdown": items_break,
            "items_total": len(its),
            "sessions_breakdown": sessions_break,
            "had_activity_in_window": p.id in active_in_window,
        })
    # Top por progreso descendente.
    top_projects.sort(key=lambda r: -r["progress"])

    # 2) Decisions timeline (sesiones por bucket en el rango).
    decisions_timeline = _build_decisions_timeline(sessions_in_window, start, end)

    # 3) Team activity + Workload — métricas POR OWNER con breakdown rico.
    #    - meetings: reuniones distintas tocadas por el owner
    #    - actions:  total action_items del owner
    #    - workload: por_owner con status_breakdown {pending, done, blocked, cancelled, overdue}
    owner_sessions: Dict[str, set] = {}
    team_activity_actions: Dict[str, int] = {}
    workload: Dict[str, Dict[str, Any]] = {}
    for it in items:
        owner = (it.owner_name or "Sin asignar").strip() or "Sin asignar"
        email = (it.owner_email or "").strip()
        if it.session_id is not None:
            owner_sessions.setdefault(owner, set()).add(it.session_id)
        team_activity_actions[owner] = team_activity_actions.get(owner, 0) + 1

        entry = workload.setdefault(owner, {
            "owner": owner,
            "email": email,
            "total": 0,
            "by_status": {"pending": 0, "done": 0, "blocked": 0, "cancelled": 0, "overdue": 0},
            "meetings": 0,
        })
        entry["total"] += 1
        entry["by_status"][it.status] = entry["by_status"].get(it.status, 0) + 1
        if it.status in ("pending", "blocked"):
            d = _parse_due_date(it.due_date)
            if d and d.replace(tzinfo=None) < now.replace(tzinfo=None):
                entry["by_status"]["overdue"] += 1
        # Mantener email más reciente si lo trae.
        if email and not entry.get("email"):
            entry["email"] = email
    team_activity_meetings = {o: len(s) for o, s in owner_sessions.items()}
    for o, count in team_activity_meetings.items():
        if o in workload:
            workload[o]["meetings"] = count

    workload_list = sorted(
        workload.values(),
        key=lambda r: -r["total"],
    )

    return {
        "period": period,
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "project_id": project_id,
        "project_name": project_names.get(project_id) if project_id else "Todos los proyectos",
        "metrics": {
            "sessions_count": len(sessions_in_window),
            "sessions_curated": sum(1 for s in sessions_in_window if s.status == "completed"),
            "sessions_pending": sum(1 for s in sessions_in_window if s.status in ("pending", "processing")),
            "action_items_total": len(items),
            "action_items_by_status": by_status,
            "tasks_completed": by_status.get("done", 0),
            "completed_in_window": len(completed_in_window),
            "auto_dispatched": sum(1 for s in sessions_in_window if s.status == "processed"),
            "decisions_count": len(sessions_in_window),  # proxy: 1 reunión = 1+ decisiones
            "overdue_tasks": sum(1 for it in items
                                 if it.status in ("pending", "blocked")
                                 and (d := _parse_due_date(it.due_date))
                                 and d.replace(tzinfo=None) < now),
            "projects_count": len(target_projects),
            "projects_active_in_window": len(active_in_window),
            "completion_rate": int(round((by_status.get("done", 0) / len(items)) * 100)) if items else 0,
            # Tasa REAL de curación: % de sesiones en la ventana ya completadas.
            "curation_rate": int(round(
                (sum(1 for s in sessions_in_window if s.status == "completed") /
                 len(sessions_in_window)) * 100
            )) if sessions_in_window else 0,
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
        # Nuevos campos.
        "projects_breakdown": [
            {"key": k, "value": v}
            for k, v in projects_breakdown_counts.items()
        ],
        "decisions_timeline": decisions_timeline,
        "top_projects": top_projects[:50],
        "team_activity": {
            "meetings": sorted(
                [{"owner": o, "value": v} for o, v in team_activity_meetings.items()],
                key=lambda x: -x["value"],
            )[:8],
            "actions": sorted(
                [{"owner": o, "value": v} for o, v in team_activity_actions.items()],
                key=lambda x: -x["value"],
            )[:8],
            # Combined rows: ambas métricas a la vez por persona — para la
            # nueva vista de filas (no más segmented control).
            "combined": [
                {
                    "owner": o,
                    "meetings": team_activity_meetings.get(o, 0),
                    "actions":  team_activity_actions.get(o, 0),
                }
                for o in sorted(
                    set(team_activity_meetings) | set(team_activity_actions),
                    key=lambda o: -(team_activity_actions.get(o, 0) + team_activity_meetings.get(o, 0)),
                )
            ][:10],
        },
        "workload": workload_list,
        "available_projects": [
            {"id": p.id, "name": p.name}
            for p in all_projects if p.is_active and p.id
        ],
    }


@router.get("/data")
def get_report_data(
    db: Session = Depends(get_session),
    period: str = Query("week", description="'week' | 'month' | 'custom'"),
    ref: Optional[str] = Query(None, description="Fecha ISO de referencia. Default: hoy."),
    project_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None, description="Solo si period='custom' — ISO yyyy-mm-dd"),
    end_date: Optional[str] = Query(None, description="Solo si period='custom' — ISO yyyy-mm-dd"),
):
    return _build_report(db, period, ref, project_id, start_date, end_date)


@router.get("/pdf")
def get_report_pdf(
    db: Session = Depends(get_session),
    period: str = Query("week"),
    ref: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Renderiza el reporte a PDF usando fpdf2 (sin dependencias externas pesadas)."""
    from fpdf import FPDF

    def _safe(text: object) -> str:
        """Sanitiza para latin-1 (la única codificación que entienden las fuentes
        built-in de fpdf2). Reemplaza acentos y caracteres especiales que no
        encajan para evitar 'Not enough horizontal space to render a single
        character' por glifos no representables."""
        if text is None:
            return ""
        return str(text).encode("latin-1", "replace").decode("latin-1")

    try:
        report = _build_report(db, period, ref, project_id, start_date, end_date)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error construyendo el reporte para PDF.")
        raise HTTPException(500, "No se pudo construir el reporte.")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    try:
        # Definimos márgenes anchos para tener celdas con ancho útil predecible.
        pdf.set_margins(left=10, top=10, right=10)
        pdf.set_x(10)

        # --- Header ---
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(190, 10, _safe("Reporte ejecutivo Notiva"), ln=1)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_x(10); pdf.cell(190, 6, _safe(report["label"]), ln=1)
        pdf.set_x(10); pdf.cell(190, 6, _safe(f"Proyecto: {report['project_name']}"), ln=1)
        pdf.set_x(10); pdf.cell(190, 6, _safe(f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"), ln=1)
        pdf.ln(4)

        # --- KPIs ---
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_x(10); pdf.cell(190, 7, _safe("Indicadores clave"), ln=1)
        pdf.set_font("Helvetica", "", 11)
        m = report["metrics"]
        kpi_rows = [
            ("Proyectos",              m.get("projects_count", 0)),
            ("Tasa de finalizacion",   f'{m.get("completion_rate", 0)}%'),
            ("Decisiones",             m.get("decisions_count", 0)),
            ("Tareas vencidas",        m.get("overdue_tasks", 0)),
            ("Reuniones",              m.get("sessions_count", 0)),
            ("Tareas totales",         m.get("action_items_total", 0)),
            ("  Pendientes",           m["action_items_by_status"].get("pending", 0)),
            ("  Bloqueadas",           m["action_items_by_status"].get("blocked", 0)),
            ("  Completadas",          m["action_items_by_status"].get("done", 0)),
            ("  Canceladas",           m["action_items_by_status"].get("cancelled", 0)),
            ("Cerradas en ventana",    m.get("completed_in_window", 0)),
            ("Auto-despachadas",       m.get("auto_dispatched", 0)),
        ]
        for label, value in kpi_rows:
            pdf.set_x(10)
            pdf.cell(110, 6, _safe(label), border=0)
            pdf.cell(80,  6, _safe(value), border=0, ln=1)
        pdf.ln(4)

        # Ancho disponible para celdas — explícito para evitar que `cell(0,...)`
        # se interprete con poco espacio si el cursor quedó descolocado.
        PAGE_W = 210  # A4 portrait mm
        LEFT_M = 10
        RIGHT_M = 10
        ROW_W = PAGE_W - LEFT_M - RIGHT_M  # 190mm útiles

        # --- Top proyectos ---
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_x(LEFT_M)
        pdf.cell(ROW_W, 7, _safe("Principales proyectos por progreso"), ln=1)
        pdf.set_font("Helvetica", "", 10)
        if not report.get("top_projects"):
            pdf.set_x(LEFT_M)
            pdf.cell(ROW_W, 6, _safe("Sin proyectos para mostrar."), ln=1)
        else:
            # Encabezado de tabla
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_x(LEFT_M)
            pdf.cell(85, 6, _safe("Proyecto"), border=0)
            pdf.cell(50, 6, _safe("Responsable"), border=0)
            pdf.cell(25, 6, _safe("Progreso"), border=0)
            pdf.cell(30, 6, _safe("Estado"), border=0, ln=1)
            pdf.set_font("Helvetica", "", 9)
            for p in report["top_projects"][:30]:
                pdf.set_x(LEFT_M)
                pdf.cell(85, 5, _safe((p['name'] or '')[:42]),    border=0)
                pdf.cell(50, 5, _safe((p['owner'] or '')[:25]),   border=0)
                pdf.cell(25, 5, _safe(f"{p['progress']}%"),       border=0)
                pdf.cell(30, 5, _safe(p['status']),               border=0, ln=1)
        pdf.ln(2)

        # --- Reuniones del período ---
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_x(LEFT_M)
        pdf.cell(ROW_W, 7, _safe("Reuniones del periodo"), ln=1)
        pdf.set_font("Helvetica", "", 9)
        if not report["sessions"]:
            pdf.set_x(LEFT_M)
            pdf.cell(ROW_W, 6, _safe("Sin reuniones en este periodo."), ln=1)
        else:
            for s in report["sessions"][:30]:
                pdf.set_x(LEFT_M)
                title = (s["title"] or "Sin titulo")[:55]
                proj  = (s["project_name"] or "")[:25]
                pdf.cell(95, 5, _safe(f"- {title}"), border=0)
                pdf.cell(60, 5, _safe(proj),        border=0)
                pdf.cell(35, 5, _safe(s["status"]), border=0, ln=1)
        pdf.ln(2)

        # --- Top responsables ---
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_x(LEFT_M)
        pdf.cell(ROW_W, 7, _safe("Responsables con mas tareas"), ln=1)
        pdf.set_font("Helvetica", "", 10)
        if not report["top_owners"]:
            pdf.set_x(LEFT_M)
            pdf.cell(ROW_W, 6, _safe("Sin responsables asignados."), ln=1)
        else:
            for o in report["top_owners"]:
                pdf.set_x(LEFT_M)
                pdf.cell(120, 6, _safe((o['owner'] or '')[:55]), border=0)
                pdf.cell(60,  6, _safe(f"{o['count']} tareas"),  border=0, ln=1)

        buffer = io.BytesIO()
        raw = pdf.output(dest="S")
        if isinstance(raw, str):
            buffer.write(raw.encode("latin-1"))
        else:
            buffer.write(bytes(raw))
        buffer.seek(0)
    except Exception:
        logger.exception("Error generando PDF.")
        raise HTTPException(500, "No se pudo generar el PDF. Revisa los logs del servidor.")

    safe_label = report["label"].replace("/", "-").replace(" ", "_")
    filename = f"Reporte_Notiva_{safe_label}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/excel")
def get_report_excel(
    db: Session = Depends(get_session),
    period: str = Query("week"),
    ref: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Exporta el mismo reporte a XLSX con varias hojas (Resumen, Métricas,
    Top Proyectos, Equipo, Reuniones, Decisiones)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    report = _build_report(db, period, ref, project_id, start_date, end_date)
    wb = Workbook()

    HEADER = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    HEADER_FILL = PatternFill("solid", fgColor="155EEF")
    CENTER = Alignment(horizontal="center", vertical="center")

    def _header_row(ws, row: int, cells: List[str]):
        for i, txt in enumerate(cells, start=1):
            c = ws.cell(row=row, column=i, value=txt)
            c.font = HEADER
            c.fill = HEADER_FILL
            c.alignment = CENTER

    # Hoja 1: Resumen
    ws = wb.active
    ws.title = "Resumen"
    ws["A1"] = "Reporte ejecutivo Notiva"
    ws["A1"].font = Font(name="Calibri", size=16, bold=True)
    ws["A3"] = "Periodo:"; ws["B3"] = report["label"]
    ws["A4"] = "Proyecto:"; ws["B4"] = report["project_name"]
    ws["A5"] = "Generado:"; ws["B5"] = datetime.now().strftime("%d/%m/%Y %H:%M")

    m = report["metrics"]
    rows_summary = [
        ("Proyectos",              m.get("projects_count", 0)),
        ("Tasa de finalización",   f'{m.get("completion_rate", 0)}%'),
        ("Decisiones",             m.get("decisions_count", 0)),
        ("Tareas vencidas",        m.get("overdue_tasks", 0)),
        ("Reuniones procesadas",   m.get("sessions_count", 0)),
        ("Tareas totales",         m.get("action_items_total", 0)),
        ("  Pendientes",           m["action_items_by_status"].get("pending", 0)),
        ("  Bloqueadas",           m["action_items_by_status"].get("blocked", 0)),
        ("  Completadas",          m["action_items_by_status"].get("done", 0)),
        ("  Canceladas",           m["action_items_by_status"].get("cancelled", 0)),
        ("Auto-despachadas",       m.get("auto_dispatched", 0)),
    ]
    _header_row(ws, 7, ["Métrica", "Valor"])
    for i, (k, v) in enumerate(rows_summary, start=8):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 28

    # Hoja 2: Top Proyectos
    ws2 = wb.create_sheet(title="Top Proyectos")
    _header_row(ws2, 1, ["ID", "Proyecto", "Responsable", "Progreso (%)", "Estado"])
    for i, r in enumerate(report.get("top_projects", []), start=2):
        ws2.cell(row=i, column=1, value=r["id"])
        ws2.cell(row=i, column=2, value=r["name"])
        ws2.cell(row=i, column=3, value=r["owner"])
        ws2.cell(row=i, column=4, value=r["progress"])
        ws2.cell(row=i, column=5, value=r["status"])
    for col, w in zip(["A", "B", "C", "D", "E"], [8, 36, 26, 14, 18]):
        ws2.column_dimensions[col].width = w

    # Hoja 3: Actividad del equipo
    ws3 = wb.create_sheet(title="Equipo")
    _header_row(ws3, 1, ["Responsable", "Por reuniones", "Por acciones"])
    meetings_map = {o["owner"]: o["value"] for o in report["team_activity"]["meetings"]}
    actions_map  = {o["owner"]: o["value"] for o in report["team_activity"]["actions"]}
    owners = sorted(set(meetings_map) | set(actions_map),
                    key=lambda o: -meetings_map.get(o, 0))
    for i, o in enumerate(owners, start=2):
        ws3.cell(row=i, column=1, value=o)
        ws3.cell(row=i, column=2, value=meetings_map.get(o, 0))
        ws3.cell(row=i, column=3, value=actions_map.get(o, 0))
    for col, w in zip(["A", "B", "C"], [28, 16, 16]):
        ws3.column_dimensions[col].width = w

    # Hoja 4: Decisiones en el tiempo
    ws4 = wb.create_sheet(title="Decisiones")
    _header_row(ws4, 1, ["Fecha (ISO)", "Etiqueta", "Decisiones (acumuladas)"])
    for i, p in enumerate(report.get("decisions_timeline", []), start=2):
        ws4.cell(row=i, column=1, value=p.get("iso"))
        ws4.cell(row=i, column=2, value=p.get("label"))
        ws4.cell(row=i, column=3, value=p.get("value"))
    for col, w in zip(["A", "B", "C"], [14, 14, 22]):
        ws4.column_dimensions[col].width = w

    # Hoja 5: Reuniones
    ws5 = wb.create_sheet(title="Reuniones")
    _header_row(ws5, 1, ["ID", "Título", "Fecha", "Estado", "Proyecto"])
    for i, s in enumerate(report.get("sessions", []), start=2):
        ws5.cell(row=i, column=1, value=s["id"])
        ws5.cell(row=i, column=2, value=s["title"])
        ws5.cell(row=i, column=3, value=s["date"])
        ws5.cell(row=i, column=4, value=s["status"])
        ws5.cell(row=i, column=5, value=s["project_name"])
    for col, w in zip(["A", "B", "C", "D", "E"], [8, 48, 22, 16, 24]):
        ws5.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    safe_label = report["label"].replace("/", "-").replace(" ", "_")
    filename = f"Reporte_Notiva_{safe_label}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

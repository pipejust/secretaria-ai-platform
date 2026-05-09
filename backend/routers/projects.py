from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, User, ProjectContact
from routers.auth import get_current_user
import crud

router = APIRouter(prefix="/api/projects", tags=["projects"])

# -----------------
# Projects
# -----------------

@router.get("/", response_model=List[Project])
def get_projects(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Obtiene la lista de todos los proyectos activos"""
    projects = crud.project.get_active_projects(session)
    return projects

@router.post("/", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(
    project: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Crea un nuevo proyecto"""
    db_project = crud.project.get_by_name(session, name=project.name)
    if db_project:
        raise HTTPException(status_code=400, detail="Ya existe un proyecto con ese nombre")
    
    return crud.project.create(session, obj_in=project)

@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: int,
    project_update: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Actualiza la información de un proyecto"""
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    return crud.project.update(session, db_obj=db_project, obj_in=project_update)

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Desactiva lógicamente un proyecto"""
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    
    crud.project.deactivate(session, db_obj=db_project)

# -----------------
# Routings (Destinations per project)
# -----------------

@router.get("/{project_id}/routings", response_model=List[Routing])
def get_project_routings(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    return db_project.routings

@router.post("/{project_id}/routings", response_model=Routing)
def add_project_routing(
    project_id: int,
    routing: Routing,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    routing.project_id = project_id
    return crud.routing.create(session, obj_in=routing)

@router.delete("/routings/{routing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routing(
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    routing_obj = crud.routing.get(session, routing_id)
    if not routing_obj:
        raise HTTPException(status_code=404, detail="Routing config not found")
        
    crud.routing.remove(session, id=routing_id)

@router.patch("/routings/{routing_id}/toggle", response_model=Routing)
def toggle_routing_status(
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    routing_obj = crud.routing.get(session, routing_id)
    if not routing_obj:
        raise HTTPException(status_code=404, detail="Routing config not found")
        
    routing_obj.is_active = not routing_obj.is_active
    session.add(routing_obj)
    session.commit()
    session.refresh(routing_obj)
    return routing_obj

# -----------------
# Sessions (Meetings per project)
# -----------------

# -----------------
# Contacts (Personas del proyecto)
# -----------------

@router.get("/{project_id}/contacts", response_model=List[ProjectContact])
def get_project_contacts(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Obtiene los contactos asociados a un proyecto"""
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    return db_project.contacts

@router.post("/{project_id}/contacts", response_model=ProjectContact)
def add_project_contact(
    project_id: int,
    contact: ProjectContact,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Agrega un nuevo contacto a un proyecto"""
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    contact.project_id = project_id
    return crud.project_contact.create(session, obj_in=contact)

@router.put("/contacts/{contact_id}", response_model=ProjectContact)
def update_project_contact(
    contact_id: int,
    contact_update: ProjectContact,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Actualiza un contacto del proyecto"""
    contact_obj = crud.project_contact.get(session, contact_id)
    if not contact_obj:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
        
    return crud.project_contact.update(session, db_obj=contact_obj, obj_in=contact_update)

@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_contact(
    contact_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Elimina un contacto"""
    contact_obj = crud.project_contact.get(session, contact_id)
    if not contact_obj:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
        
    crud.project_contact.remove(session, id=contact_id)

@router.get("/{project_id}/sessions", response_model=List[MeetingSession])
def get_project_sessions(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Obtiene las sesiones de un proyecto"""
    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return db_project.sessions


@router.get("/{project_id}/dashboard")
def get_project_dashboard(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Resumen del proyecto: contactos, últimas sesiones, decisiones recientes,
    tareas activas y métricas. Usado por la pantalla `/admin/projects/:id`."""
    from sqlmodel import select
    from datetime import datetime
    from models import ActionItem, MeetingSession, ProjectContact, Routing, User as UserModel

    db_project = crud.project.get(session, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    sessions_raw = session.exec(
        select(MeetingSession)
        .where(MeetingSession.project_id == project_id)
        .order_by(MeetingSession.id.desc())
    ).all()

    last_sessions = [
        {
            "id": s.id,
            "title": s.title,
            "date": s.date,
            "status": s.status,
            "language": s.language,
        }
        for s in sessions_raw[:10]
    ]

    # Decisiones recientes: tomar las últimas 5 sesiones y devolver sus
    # processed_decisions formateadas en viñetas (ya vienen así desde Groq).
    recent_decisions: list[dict] = []
    for s in sessions_raw[:5]:
        if s.processed_decisions and s.processed_decisions.strip():
            recent_decisions.append({
                "session_id": s.id,
                "session_title": s.title,
                "session_date": s.date,
                "text": s.processed_decisions,
            })

    # Tareas activas (status pending/blocked, todas las sesiones del proyecto)
    session_ids = [s.id for s in sessions_raw if s.id is not None]
    active_action_items: list[dict] = []
    overdue_count = 0
    upcoming_count = 0
    completed_count = 0
    if session_ids:
        all_items = session.exec(
            select(ActionItem).where(ActionItem.session_id.in_(session_ids))
        ).all()
        now = datetime.now()
        for item in all_items:
            if item.status == "done":
                completed_count += 1
                continue
            if item.status == "cancelled":
                continue
            due_dt = None
            if item.due_date:
                try:
                    due_dt = datetime.fromisoformat(str(item.due_date).replace("Z", "+00:00"))
                except ValueError:
                    try:
                        due_dt = datetime.strptime(str(item.due_date)[:10], "%Y-%m-%d")
                    except ValueError:
                        due_dt = None
            is_overdue = due_dt is not None and due_dt < now
            is_upcoming = due_dt is not None and not is_overdue and (due_dt - now).days <= 7
            if is_overdue:
                overdue_count += 1
            if is_upcoming:
                upcoming_count += 1
            active_action_items.append({
                "id": item.id,
                "session_id": item.session_id,
                "title": item.title,
                "owner_name": item.owner_name,
                "owner_email": item.owner_email,
                "due_date": item.due_date,
                "status": item.status,
                "is_overdue": is_overdue,
                "is_upcoming": is_upcoming,
            })
        active_action_items.sort(
            key=lambda r: (
                0 if r["is_overdue"] else (1 if r["is_upcoming"] else 2),
                r["due_date"] or "9999-12-31",
            )
        )

    contacts = session.exec(
        select(ProjectContact).where(ProjectContact.project_id == project_id)
    ).all()
    routings = session.exec(
        select(Routing).where(Routing.project_id == project_id)
    ).all()

    owner_user = None
    if db_project.owner_user_id:
        u = session.get(UserModel, db_project.owner_user_id)
        if u:
            owner_user = {"id": u.id, "full_name": u.full_name, "email": u.email}

    return {
        "project": {
            "id": db_project.id,
            "name": db_project.name,
            "description": db_project.description,
            "is_active": db_project.is_active,
            "owner_user": owner_user,
            "auto_dispatch_enabled": db_project.auto_dispatch_enabled,
            "auto_dispatch_timeout_hours": db_project.auto_dispatch_timeout_hours,
        },
        "metrics": {
            "total_sessions": len(sessions_raw),
            "active_action_items": len(active_action_items),
            "overdue": overdue_count,
            "upcoming_7d": upcoming_count,
            "completed_action_items": completed_count,
            "contacts_count": len(contacts),
            "routings_active": sum(1 for r in routings if r.is_active),
        },
        "last_sessions": last_sessions,
        "recent_decisions": recent_decisions,
        "active_action_items": active_action_items[:50],
        "contacts": [
            {"id": c.id, "name": c.name, "email": c.email, "role": c.role, "entity": c.entity}
            for c in contacts
        ],
    }

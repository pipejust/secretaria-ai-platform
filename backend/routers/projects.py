from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, Tenant, User, ProjectContact
from routers.auth import get_current_user, get_current_tenant, require_admin
import crud

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _get_project_or_404(session: Session, project_id: int, tenant: Tenant) -> Project:
    """Helper de aislamiento: 404 si el proyecto no es de este tenant."""
    p = session.get(Project, project_id)
    if not p or p.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return p


# -----------------
# Projects
# -----------------

@router.get("/", response_model=List[Project])
def get_projects(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Lista proyectos activos del tenant actual."""
    rows = session.exec(
        select(Project)
        .where(Project.tenant_id == tenant.id)
        .where(Project.is_active == True)  # noqa: E712
        .order_by(Project.id.asc())
    ).all()
    return rows


@router.post("/", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(
    project: Project,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Crea un nuevo proyecto en el tenant del usuario. SOLO admins."""
    existing = session.exec(
        select(Project)
        .where(Project.tenant_id == tenant.id)
        .where(Project.name == project.name)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un proyecto con ese nombre en esta empresa")
    project.tenant_id = tenant.id
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: int,
    project_update: Project,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza la información de un proyecto del tenant. SOLO admins."""
    db_project = _get_project_or_404(session, project_id, tenant)
    # No permitimos cambiar el tenant_id desde un PUT.
    update_data = project_update.model_dump(exclude_unset=True)
    update_data.pop("tenant_id", None)
    update_data.pop("id", None)
    for k, v in update_data.items():
        setattr(db_project, k, v)
    session.add(db_project)
    session.commit()
    session.refresh(db_project)
    return db_project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Desactiva lógicamente un proyecto del tenant. SOLO admins."""
    db_project = _get_project_or_404(session, project_id, tenant)
    db_project.is_active = False
    session.add(db_project)
    session.commit()

# -----------------
# Routings (Destinations per project)
# -----------------

@router.get("/{project_id}/routings", response_model=List[Routing])
def get_project_routings(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Lista los routings del proyecto que pertenecen al usuario actual.

    Per-user: cada miembro define SUS propias rutas dentro del proyecto.
    Filas legacy sin user_id quedan ocultas (las migra el script de
    arranque al primer admin del tenant)."""
    _get_project_or_404(session, project_id, tenant)
    rows = session.exec(
        select(Routing)
        .where(Routing.project_id == project_id)
        .where(Routing.user_id == current_user.id)
    ).all()
    return rows


@router.post("/{project_id}/routings", response_model=Routing)
def add_project_routing(
    project_id: int,
    routing: Routing,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Crea un routing que pertenece al usuario actual. Forzamos
    project_id y user_id server-side para que el cliente no pueda
    falsificar el dueño del routing."""
    _get_project_or_404(session, project_id, tenant)
    routing.project_id = project_id
    routing.user_id = current_user.id
    return crud.routing.create(session, obj_in=routing)


def _get_routing_for_user_or_404(
    session: Session,
    routing_id: int,
    tenant: Tenant,
    current_user: User,
    project_id: Optional[int] = None,
) -> Routing:
    """Carga un Routing verificando dueño + tenant + proyecto.

    Reglas de acceso (CRÍTICO):
      1. El routing debe existir.
      2. Si la URL trae project_id, debe coincidir con routing.project_id.
      3. El proyecto del routing debe ser del tenant del caller.
      4. routing.user_id debe coincidir con current_user.id — un usuario
         NUNCA puede tocar el routing de otro, ni siquiera del mismo
         proyecto. (Los admins TAMPOCO; si quieren operar las rutas
         personales, se hace por el panel del usuario.)
    """
    routing_obj = crud.routing.get(session, routing_id)
    if not routing_obj:
        raise HTTPException(status_code=404, detail="Routing config not found")
    if project_id is not None and routing_obj.project_id != project_id:
        raise HTTPException(status_code=404, detail="Routing config not found")
    _get_project_or_404(session, routing_obj.project_id, tenant)
    if routing_obj.user_id != current_user.id:
        # 404 (no 403) para no filtrar que existe un routing ajeno.
        raise HTTPException(status_code=404, detail="Routing config not found")
    return routing_obj


@router.delete("/{project_id}/routings/{routing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_routing(
    project_id: int,
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Elimina un routing del usuario actual. Ruta nested RESTful — la
    que llama el frontend. Antes el backend solo tenía la versión flat,
    por eso DELETE devolvía 404 silencioso."""
    _get_routing_for_user_or_404(
        session, routing_id, tenant, current_user, project_id=project_id,
    )
    crud.routing.remove(session, id=routing_id)


@router.delete("/routings/{routing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routing(
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Alias flat — compatibilidad con clientes viejos."""
    _get_routing_for_user_or_404(session, routing_id, tenant, current_user)
    crud.routing.remove(session, id=routing_id)


@router.patch("/{project_id}/routings/{routing_id}/toggle", response_model=Routing)
def toggle_project_routing_status(
    project_id: int,
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Toggle is_active sobre un routing del usuario actual."""
    routing_obj = _get_routing_for_user_or_404(
        session, routing_id, tenant, current_user, project_id=project_id,
    )
    routing_obj.is_active = not routing_obj.is_active
    session.add(routing_obj)
    session.commit()
    session.refresh(routing_obj)
    return routing_obj


@router.patch("/routings/{routing_id}/toggle", response_model=Routing)
def toggle_routing_status(
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Alias flat — compatibilidad con clientes viejos."""
    routing_obj = _get_routing_for_user_or_404(
        session, routing_id, tenant, current_user,
    )
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
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Obtiene los contactos asociados a un proyecto"""
    db_project = _get_project_or_404(session, project_id, tenant)
        
    return db_project.contacts

@router.post("/{project_id}/contacts", response_model=ProjectContact)
def add_project_contact(
    project_id: int,
    contact: ProjectContact,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
):
    """Agrega un nuevo contacto a un proyecto"""
    db_project = _get_project_or_404(session, project_id, tenant)
        
    contact.project_id = project_id
    return crud.project_contact.create(session, obj_in=contact)

@router.put("/contacts/{contact_id}", response_model=ProjectContact)
def update_project_contact(
    contact_id: int,
    contact_update: ProjectContact,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
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
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant)
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
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Sesiones del proyecto (tenant-scoped, excluye archivadas)."""
    from sqlmodel import select
    db_project = _get_project_or_404(session, project_id, tenant)

    rows = session.exec(
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant.id)
        .where(MeetingSession.project_id == project_id)
        .where(MeetingSession.status != "archived")
        .order_by(MeetingSession.id.desc())
    ).all()
    return rows


@router.get("/{project_id}/dashboard")
def get_project_dashboard(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Resumen del proyecto: contactos, últimas sesiones, decisiones recientes,
    tareas activas y métricas. Usado por la pantalla `/admin/projects/:id`."""
    from sqlmodel import select
    from datetime import datetime
    from models import ActionItem, MeetingSession, ProjectContact, Routing, User as UserModel

    db_project = _get_project_or_404(session, project_id, tenant)

    # Multi-tenant: las queries que siguen también deben filtrar por tenant
    # para no leer sesiones/tareas/contactos de otra empresa.
    sessions_raw = session.exec(
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant.id)
        .where(MeetingSession.project_id == project_id)
        .where(MeetingSession.status != "archived")
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

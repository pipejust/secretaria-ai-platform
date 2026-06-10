from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, Tenant, User, ProjectContact
from routers.auth import get_current_user, get_current_tenant, require_admin
import crud


class RoutingUpdate(BaseModel):
    """Body de PUT/PATCH para editar una ruta de integración del usuario.

    Campos opcionales: el cliente solo manda los que quiere cambiar. NO
    aceptamos `project_id` ni `user_id` — el dueño y el proyecto vienen
    del path y de current_user, nunca del body. Esto cierra el vector
    de un user falsificando el dueño con un PUT manual.
    """
    destination_type: Optional[str] = None
    destination_config: Optional[str] = None
    is_active: Optional[bool] = None


def _is_tenant_owner(tenant: Tenant, user: User) -> bool:
    """True si `user` es el owner del `tenant`. Tolera owner_user_id NULL
    (caso legacy pre-backfill: devuelve False, los switches share quedan
    inertes hasta que la migración asigne owner)."""
    return tenant.owner_user_id is not None and tenant.owner_user_id == user.id


def _effective_routing_user_id(tenant: Tenant, current_user: User) -> int:
    """User_id sobre el que se filtra/crea routings según los switches.

    Si `share_routings=ON`, todas las operaciones de lectura usan
    `owner_user_id` (los demás ven las rutas del owner). Las operaciones
    de escritura se rechazan con 403 antes de llegar acá.

    Si `share_routings=OFF`, cada user opera con sus propias rutas
    (modelo per-user puro)."""
    if tenant.share_routings and tenant.owner_user_id:
        return tenant.owner_user_id
    return current_user.id


def _require_routing_write_permission(tenant: Tenant, current_user: User) -> None:
    """Lanza 403 si `share_routings=ON` y el caller no es el owner.

    Solo bloquea escritura — la lectura está permitida para todos (los
    demás ven las rutas del owner en read-only)."""
    if tenant.share_routings and not _is_tenant_owner(tenant, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El dueño del tenant administra las rutas de integración. "
                "Solo el owner puede modificarlas."
            ),
        )

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
    """Lista los routings del proyecto.

    Si `share_routings=ON`, devuelve las del owner para TODO el equipo.
    Si OFF, las del current_user (modelo per-user)."""
    _get_project_or_404(session, project_id, tenant)
    target_user_id = _effective_routing_user_id(tenant, current_user)
    rows = session.exec(
        select(Routing)
        .where(Routing.project_id == project_id)
        .where(Routing.user_id == target_user_id)
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
    """Crea un routing. Si `share_routings=ON` y caller no es owner → 403.

    En cualquier caso se asigna a `current_user.id` server-side. Cuando
    el owner crea con share_routings=ON, esa fila ES la compartida con
    el resto del equipo."""
    _require_routing_write_permission(tenant, current_user)
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

    Reglas de acceso:
      1. El routing debe existir.
      2. Si la URL trae project_id, debe coincidir.
      3. El proyecto del routing debe ser del tenant del caller.
      4. Si `share_routings=ON`: el routing debe pertenecer al OWNER
         del tenant — sino 404 (no filtra existencia de rutas ajenas).
         La autorización (solo owner edita) se valida en cada endpoint
         de escritura ANTES de llegar acá.
      5. Si `share_routings=OFF`: el routing debe pertenecer al
         current_user — modelo per-user.
    """
    routing_obj = crud.routing.get(session, routing_id)
    if not routing_obj:
        raise HTTPException(status_code=404, detail="Routing config not found")
    if project_id is not None and routing_obj.project_id != project_id:
        raise HTTPException(status_code=404, detail="Routing config not found")
    _get_project_or_404(session, routing_obj.project_id, tenant)
    expected_owner = _effective_routing_user_id(tenant, current_user)
    if routing_obj.user_id != expected_owner:
        raise HTTPException(status_code=404, detail="Routing config not found")
    return routing_obj


@router.put("/{project_id}/routings/{routing_id}", response_model=Routing)
def update_project_routing(
    project_id: int,
    routing_id: int,
    payload: RoutingUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Edita una ruta de integración del usuario actual.

    El cliente puede cambiar destination_type, destination_config y/o
    is_active. project_id y user_id quedan fijos: si quisieras "mover"
    una ruta a otro proyecto, lo correcto es borrarla y crear una nueva
    en el proyecto destino (las credenciales pueden no aplicar).

    Si el body trae destination_config, validamos que sea JSON parseable
    para no almacenar basura que después rompa el dispatch."""
    _require_routing_write_permission(tenant, current_user)
    routing_obj = _get_routing_for_user_or_404(
        session, routing_id, tenant, current_user, project_id=project_id,
    )

    if payload.destination_type is not None:
        dt = payload.destination_type.strip()
        if not dt:
            raise HTTPException(status_code=422, detail="destination_type no puede ser vacío")
        routing_obj.destination_type = dt

    if payload.destination_config is not None:
        cfg = payload.destination_config
        # Aceptamos string ya JSON-encoded para mantener compat con el
        # POST original que recibe Routing crudo (destination_config: str).
        try:
            import json as _json
            _json.loads(cfg or "{}")
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="destination_config debe ser JSON válido",
            )
        routing_obj.destination_config = cfg

    if payload.is_active is not None:
        routing_obj.is_active = bool(payload.is_active)

    session.add(routing_obj)
    session.commit()
    session.refresh(routing_obj)
    return routing_obj


@router.put("/routings/{routing_id}", response_model=Routing)
def update_routing(
    routing_id: int,
    payload: RoutingUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Alias flat del PUT — compat con clientes viejos."""
    _require_routing_write_permission(tenant, current_user)
    routing_obj = _get_routing_for_user_or_404(
        session, routing_id, tenant, current_user,
    )

    if payload.destination_type is not None:
        dt = payload.destination_type.strip()
        if not dt:
            raise HTTPException(status_code=422, detail="destination_type no puede ser vacío")
        routing_obj.destination_type = dt

    if payload.destination_config is not None:
        try:
            import json as _json
            _json.loads(payload.destination_config or "{}")
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="destination_config debe ser JSON válido",
            )
        routing_obj.destination_config = payload.destination_config

    if payload.is_active is not None:
        routing_obj.is_active = bool(payload.is_active)

    session.add(routing_obj)
    session.commit()
    session.refresh(routing_obj)
    return routing_obj


@router.delete("/{project_id}/routings/{routing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_routing(
    project_id: int,
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Elimina un routing. Si `share_routings=ON` y caller no es owner → 403."""
    _require_routing_write_permission(tenant, current_user)
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
    _require_routing_write_permission(tenant, current_user)
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
    """Toggle is_active. Si `share_routings=ON` y caller no es owner → 403."""
    _require_routing_write_permission(tenant, current_user)
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
    _require_routing_write_permission(tenant, current_user)
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

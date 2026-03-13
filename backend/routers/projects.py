from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, User, ProjectContact
from routers.auth import get_current_user
import crud

router = APIRouter(prefix="/projects", tags=["projects"])

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

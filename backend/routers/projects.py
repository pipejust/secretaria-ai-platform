from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, User
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

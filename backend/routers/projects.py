from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from typing import List, Optional

from database import get_session
from models import Project, Routing, MeetingSession, User
from routers.auth import get_current_user

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
    projects = session.exec(select(Project).where(Project.is_active == True)).all()
    return projects

@router.post("/", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(
    project: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Crea un nuevo proyecto"""
    db_project = session.exec(select(Project).where(Project.name == project.name)).first()
    if db_project:
        raise HTTPException(status_code=400, detail="Ya existe un proyecto con ese nombre")
    
    session.add(project)
    session.commit()
    session.refresh(project)
    return project

@router.put("/{project_id}", response_model=Project)
def update_project(
    project_id: int,
    project_update: Project,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Actualiza la información de un proyecto"""
    db_project = session.get(Project, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    db_project.name = project_update.name
    db_project.is_active = project_update.is_active
    
    session.add(db_project)
    session.commit()
    session.refresh(db_project)
    return db_project

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    """Desactiva lógicamente un proyecto"""
    db_project = session.get(Project, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    
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
    current_user: User = Depends(get_current_user)
):
    db_project = session.get(Project, project_id)
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
    db_project = session.get(Project, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    routing.project_id = project_id
    session.add(routing)
    session.commit()
    session.refresh(routing)
    return routing

@router.delete("/routings/{routing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routing(
    routing_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    routing = session.get(Routing, routing_id)
    if not routing:
        raise HTTPException(status_code=404, detail="Routing config not found")
        
    session.delete(routing)
    session.commit()

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
    db_project = session.get(Project, project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        
    return db_project.sessions

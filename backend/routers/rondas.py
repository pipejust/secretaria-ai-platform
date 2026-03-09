from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter(
    prefix="/api/v1",
    tags=["Rondas"]
)

class RondaArea(BaseModel):
    id: str
    name: str

class Ronda(BaseModel):
    id: str
    area_id: str
    session_id: str
    status: str

@router.get("/rondas_areas", response_model=List[RondaArea])
def get_rondas_areas():
    return [
        {"id": "a1", "name": "Finanzas"},
        {"id": "a2", "name": "Legal"},
        {"id": "a3", "name": "Directorio"}
    ]

@router.get("/rondas", response_model=List[Ronda])
def get_rondas():
    return [
        {"id": "r1", "area_id": "a1", "session_id": "1", "status": "pending"}
    ]

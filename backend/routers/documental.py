from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

router = APIRouter(
    prefix="/api/v1",
    tags=["Documental"]
)

class DocumentItem(BaseModel):
    id: str
    filename: str
    url: str
    size: str
    uploaded_at: str

@router.get("/documental_files", response_model=List[DocumentItem])
def get_documental_files():
    # Return mockup data to fix frontend 500/404 errors until DB is fully implemented
    return [
        {
            "id": "1",
            "filename": "Acta_Directorio_Marzo.pdf",
            "url": "https://example.com/acta1.pdf",
            "size": "2.4 MB",
            "uploaded_at": "2024-03-09T10:00:00Z"
        }
    ]

@router.get("/documental_files/url")
def get_document_url(file_id: str):
    return {"url": f"https://example.com/files/{file_id}.pdf"}

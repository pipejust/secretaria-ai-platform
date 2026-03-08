from fastapi import APIRouter, Depends, Form
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter()

@router.post("/debug_login")
def debug_login(form_data: OAuth2PasswordRequestForm = Depends()):
    print("INSIDE DEBUG LOGIN")
    return {"message": "success", "user": form_data.username}

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select
from datetime import timedelta

from database import get_session
from models import User, Role
from auth_utils import verify_password, create_access_token, get_password_hash, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from jose import JWTError, jwt
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["Autenticación"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

class LoginRequest(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role_id: int

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_session)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.exec(select(User).where(User.email == email)).first()
    if user is None:
        raise credentials_exception
    return user

def require_admin(current_user: User = Depends(get_current_user)):
    if not current_user.role or current_user.role.name != "admin":
        raise HTTPException(status_code=403, detail="Not enough privileges. Admin required.")
    return current_user


@router.post("/login")
def login_for_access_token(login_req: LoginRequest, db: Session = Depends(get_session)):
    print(f"--- DEBUG LOGIN ---")
    print(f"Username received: '{login_req.username}'")
    user = db.exec(select(User).where(User.email == login_req.username)).first()
    print(f"User found in DB: {user is not None}")
    if user:
        is_valid = verify_password(login_req.password, user.hashed_password)
        print(f"Password valid: {is_valid}")
    
    if not user or not verify_password(login_req.password, user.hashed_password):
        print("--- LOGIN FAILED ---")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    print("--- LOGIN SUCCESS ---")
        
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # in JWT payload, typically "sub" is used for identify subject
    access_token = create_access_token(
        data={"sub": user.email, "role": user.role.name if user.role else ""}, 
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/register/admin-only")
def register_user(user_in: UserCreate, db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Solo un Admin puede crear usuarios nuevos"""
    existing_user = db.exec(select(User).where(User.email == user_in.email)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role_id=user_in.role_id
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"msg": "User created successfully", "user_id": user.id}

@router.get("/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    return {
        "email": current_user.email, 
        "full_name": current_user.full_name, 
        "role": current_user.role.name if current_user.role else None
    }

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

@router.put("/password")
def change_password(pass_req: PasswordChangeRequest, db: Session = Depends(get_session), current_user: User = Depends(get_current_user)):
    if not verify_password(pass_req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="La contraseña actual es incorrecta")
    
    current_user.hashed_password = get_password_hash(pass_req.new_password)
    db.add(current_user)
    db.commit()
    return {"msg": "Contraseña actualizada exitosamente"}

class RoleCreate(BaseModel):
    name: str
    description: str

@router.get("/roles")
def get_roles(db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Solo administradores pueden listar roles"""
    roles = db.exec(select(Role)).all()
    return roles

@router.post("/roles")
def create_role(role_in: RoleCreate, db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Solo un Admin puede crear un nuevo Rol"""
    existing = db.exec(select(Role).where(Role.name == role_in.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El rol ya existe")
    
    new_role = Role(name=role_in.name, description=role_in.description)
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role

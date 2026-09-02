from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.core.security import hash_password
from app.models.user import Role, User
from app.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _get_owned_user(db: Session, tenant_id: int, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
    return user


@router.get("", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> list[User]:
    return db.query(User).filter(User.tenant_id == tenant_id).all()


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> User:
    """
    El ADMIN de un colegio da de alta cuentas para su propio equipo: conductores,
    monitores y padres de familia. Siempre quedan en el mismo tenant que el admin
    que las crea (nunca se recibe tenant_id del cliente).
    """
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El correo ya está registrado")

    user = User(
        tenant_id=tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> User:
    """
    Edita datos de una cuenta del propio colegio, o la desactiva/reactiva
    (`is_active`). Desactivar es preferible a borrar: conserva el historial
    (viajes, alertas) del conductor/monitor y evita que un padre pierda de
    vista a un hijo cuyo acudiente quedó desactivado por error. Un usuario
    desactivado ya no puede iniciar sesión (`app/api/v1/auth.py` revisa
    `is_active` en el login).
    """
    user = _get_owned_user(db, tenant_id, user_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import Role, User

# tokenUrl es solo informativo para la UI de /docs; el login real se hace con JSON en /auth/login
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar la credencial",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception

    return user


def get_current_tenant_id(current_user: User = Depends(get_current_user)) -> int:
    """
    Punto único donde se obtiene el tenant_id del usuario autenticado.
    Todo endpoint que lea o escriba datos de negocio debe filtrar/asignar
    por este tenant_id, nunca confiar en un tenant_id que venga en el body.
    """
    return current_user.tenant_id


def require_roles(*allowed_roles: Role) -> Callable[[User], User]:
    """Fábrica de dependencias: exige que el usuario autenticado tenga uno de los roles dados."""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Se requiere uno de los roles: {[r.value for r in allowed_roles]}",
            )
        return current_user

    return dependency


def require_super_admin(token: str | None = Depends(oauth2_scheme)) -> dict:
    """
    Protege los endpoints de `app/api/v1/platform.py` (operador de plataforma:
    ver/crear colegios y entrar a administrar cualquiera). Es un token
    completamente aparte del de un usuario normal -- no tiene `tenant_id` ni
    corresponde a ninguna fila de `users` (no existe un "usuario" operador de
    plataforma, solo la clave compartida `PLATFORM_BOOTSTRAP_KEY`) -- por eso
    esta dependencia no reutiliza `get_current_user`, que sí espera un `sub`
    que sea un id de usuario real.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar la sesión de operador de plataforma",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception

    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "SUPER_ADMIN":
        raise credentials_exception

    return payload

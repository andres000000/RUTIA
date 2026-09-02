import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuthCodePurpose(str, enum.Enum):
    LOGIN_2FA = "LOGIN_2FA"  # segundo factor al iniciar sesión como ADMIN
    PASSWORD_RESET = "PASSWORD_RESET"  # recuperación de contraseña olvidada


class AuthCode(Base):
    """
    Código de un solo uso enviado por correo (6 dígitos), usado tanto para el
    segundo factor de autenticación del ADMIN como para recuperar contraseña.

    Nunca se guarda el código en texto plano: se guarda su hash (igual que una
    contraseña), así que ni siquiera con acceso a la base de datos se puede ver
    el código -- solo se puede comparar contra uno que alguien proponga.

    `attempts` limita cuántas veces se puede intentar adivinar el código antes
    de invalidarlo, y `expires_at` limita cuánto tiempo sigue siendo válido,
    para que un código filtrado o interceptado no sirva indefinidamente.
    """

    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[AuthCodePurpose] = mapped_column(
        Enum(AuthCodePurpose, name="auth_code_purpose"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<AuthCode id={self.id} user_id={self.user_id} purpose={self.purpose}>"

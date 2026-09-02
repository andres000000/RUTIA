"""
Envío de correos transaccionales (códigos de verificación de RUTIA).

Se usa Gmail SMTP con una "contraseña de aplicación" (no la contraseña normal
de la cuenta) porque es la forma más simple de tener envío de correo real
funcionando en un piloto académico, sin depender de crear una cuenta nueva en
un proveedor externo de correo transaccional.

Si `SMTP_USER`/`SMTP_APP_PASSWORD` no están configurados (por ejemplo, en las
pruebas automatizadas, o si alguien clona el repo sin configurar su .env), el
envío se "simula": el código queda en el log del servidor en vez de fallar la
petición. Esto evita que todo el flujo de login/recuperación se caiga por no
tener credenciales de correo — importante porque el login normal de ADMIN
pasa por aquí.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.models.auth_code import AuthCodePurpose

logger = logging.getLogger("rutia.email")

_SUBJECT_BY_PURPOSE = {
    AuthCodePurpose.LOGIN_2FA: "Tu código de verificación de RUTIA",
    AuthCodePurpose.PASSWORD_RESET: "Recupera tu contraseña de RUTIA",
}

_INTRO_BY_PURPOSE = {
    AuthCodePurpose.LOGIN_2FA: "Alguien está iniciando sesión en el panel de administración de RUTIA con tu cuenta.",
    AuthCodePurpose.PASSWORD_RESET: "Recibimos una solicitud para restablecer la contraseña de tu cuenta RUTIA.",
}


def _build_message(to_email: str, code: str, purpose: AuthCodePurpose) -> EmailMessage:
    subject = _SUBJECT_BY_PURPOSE[purpose]
    intro = _INTRO_BY_PURPOSE[purpose]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_USER}>"
    message["To"] = to_email

    message.set_content(
        f"{intro}\n\n"
        f"Tu código de verificación es: {code}\n\n"
        f"Vence en {settings.AUTH_CODE_EXPIRE_MINUTES} minutos. Si no fuiste tú, ignora este correo "
        f"y tu cuenta seguirá segura (nadie puede hacer nada sin este código)."
    )
    message.add_alternative(
        f"""\
<div style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 480px; margin: 0 auto;">
  <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 24px; border-radius: 12px 12px 0 0;">
    <h1 style="color: #ffffff; margin: 0; font-size: 20px;">RUTIA</h1>
  </div>
  <div style="border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px; padding: 24px;">
    <p style="color: #111827; font-size: 15px;">{intro}</p>
    <div style="background: #f3f4f6; border-radius: 8px; padding: 16px; text-align: center; margin: 20px 0;">
      <span style="font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #1d4ed8;">{code}</span>
    </div>
    <p style="color: #6b7280; font-size: 13px;">
      Vence en {settings.AUTH_CODE_EXPIRE_MINUTES} minutos. Si no fuiste tú, ignora este correo:
      tu cuenta sigue segura, nadie puede hacer nada sin este código.
    </p>
  </div>
</div>
""",
        subtype="html",
    )
    return message


def send_otp_email(to_email: str, code: str, purpose: AuthCodePurpose) -> None:
    """Envía el código de verificación por correo. Nunca lanza una excepción
    por un fallo de red/SMTP hacia el llamador de login (para no bloquear
    todo el sistema si Gmail tiene un problema puntual) -- en su lugar deja
    constancia en el log. Las rutas que sí necesitan saber si el correo salió
    (por ejemplo, para no dejar creer al usuario que "ya se envió" cuando no
    fue así) pueden revisar el valor de retorno más adelante si hace falta;
    por ahora, para el alcance de este piloto, se prioriza que el flujo nunca
    se caiga por un problema de correo."""
    if not settings.SMTP_USER or not settings.SMTP_APP_PASSWORD:
        logger.warning(
            "SMTP no configurado -- código de %s para %s no se envió por correo: %s",
            purpose.value,
            to_email,
            code,
        )
        return

    message = _build_message(to_email, code, purpose)
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(settings.SMTP_USER, settings.SMTP_APP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        logger.exception("No se pudo enviar el correo de verificación a %s", to_email)

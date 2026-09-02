"""
Envío del código de verificación por SMS (Twilio), como canal adicional al
correo (`app/services/email.py`) -- se disparan los dos a la vez desde
`app/services/auth_codes.py`, así que si uno falla (o no está configurado
todavía) el otro sigue funcionando como respaldo.

Igual que con el correo: si no hay credenciales de Twilio configuradas, o el
usuario no tiene teléfono registrado, el envío se "simula" (el código queda
en el log del servidor) en vez de fallar la petición -- el login y la
recuperación de contraseña nunca dependen de que el SMS salga bien.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.models.auth_code import AuthCodePurpose

logger = logging.getLogger("rutia.sms")

_MESSAGE_BY_PURPOSE = {
    AuthCodePurpose.LOGIN_2FA: "RUTIA: tu codigo para iniciar sesion es {code}. Vence en {minutes} min.",
    AuthCodePurpose.PASSWORD_RESET: "RUTIA: tu codigo para recuperar tu contrasena es {code}. Vence en {minutes} min.",
}


def normalize_phone(raw_phone: str) -> str:
    """Convierte un número al formato E.164 que exige Twilio (ej.
    '+573155421646'). Los administradores de este piloto son colegios
    colombianos, así que un número local de 10 dígitos que empieza en '3'
    (celular colombiano) se asume de Colombia y se le antepone '+57'. Si el
    número ya viene con '+' (por si en el futuro se registra uno de otro
    país), se respeta tal cual."""
    stripped = raw_phone.strip()
    digits = "".join(ch for ch in stripped if ch.isdigit())
    if stripped.startswith("+"):
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("3"):
        return f"+57{digits}"
    return f"+{digits}"


def send_otp_sms(to_phone: str | None, code: str, purpose: AuthCodePurpose) -> None:
    if not to_phone:
        return  # este usuario no tiene teléfono registrado; el correo sigue siendo el canal

    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN or not settings.TWILIO_FROM_NUMBER:
        logger.warning(
            "Twilio no configurado -- código de %s para %s no se envió por SMS: %s",
            purpose.value,
            to_phone,
            code,
        )
        return

    body = _MESSAGE_BY_PURPOSE[purpose].format(code=code, minutes=settings.AUTH_CODE_EXPIRE_MINUTES)
    try:
        # Import perezoso: así un entorno que no tenga el paquete `twilio`
        # instalado (o que simplemente no use SMS todavía) no se rompe al
        # arrancar la aplicación ni al correr las pruebas automatizadas.
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(to=normalize_phone(to_phone), from_=settings.TWILIO_FROM_NUMBER, body=body)
    except Exception:
        logger.exception("No se pudo enviar el SMS de verificación a %s", to_phone)

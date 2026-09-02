"""
Utilidad compartida entre archivos de prueba: captura los códigos de
verificación que normalmente se enviarían por correo (login 2FA de ADMIN y
recuperación de contraseña), para que las pruebas puedan usarlos sin mandar
ningún correo real.

El envío real se intercepta en `conftest.py` (fixture `_capture_otp_codes`,
autouse=True), que llena `SENT_CODES` en cada prueba. Cualquier archivo de
pruebas que necesite completar un login de ADMIN puede usar `last_code_for`.
"""

SENT_CODES: list[tuple[str, str, object]] = []


def last_code_for(email: str) -> str:
    for to_email, code, _purpose in reversed(SENT_CODES):
        if to_email == email:
            return code
    raise AssertionError(f"No se capturó ningún código de verificación enviado a {email}")

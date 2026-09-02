"""
Pruebas del canal de SMS (Twilio) para el código de verificación -- canal
adicional al correo, agregado a pedido del usuario para que el 2FA/reset de
contraseña también pueda llegar a un celular.
"""

from app.core.config import settings
from app.models.auth_code import AuthCodePurpose
from app.services import sms


def test_normalize_phone_local_colombian_number():
    assert sms.normalize_phone("3155421646") == "+573155421646"


def test_normalize_phone_with_spaces_and_dashes():
    assert sms.normalize_phone("315 542-1646") == "+573155421646"


def test_normalize_phone_already_in_e164():
    assert sms.normalize_phone("+573155421646") == "+573155421646"


def test_send_otp_sms_does_nothing_without_a_phone_number(caplog):
    # No debe lanzar ninguna excepción ni intentar nada si el usuario no tiene
    # teléfono registrado -- el correo sigue siendo el canal para esa cuenta.
    sms.send_otp_sms(None, "123456", AuthCodePurpose.LOGIN_2FA)
    sms.send_otp_sms("", "123456", AuthCodePurpose.LOGIN_2FA)


def test_send_otp_sms_falls_back_to_log_when_twilio_is_not_configured(caplog):
    assert settings.TWILIO_ACCOUNT_SID == ""
    with caplog.at_level("WARNING", logger="rutia.sms"):
        sms.send_otp_sms("3155421646", "654321", AuthCodePurpose.LOGIN_2FA)
    assert "Twilio no configurado" in caplog.text
    assert "654321" in caplog.text


def test_send_otp_sms_calls_twilio_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACxxxx")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "authtoken")
    monkeypatch.setattr(settings, "TWILIO_FROM_NUMBER", "+15005550006")

    created_messages = []

    class FakeMessages:
        def create(self, to, from_, body):
            created_messages.append({"to": to, "from_": from_, "body": body})

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            self.messages = FakeMessages()

    import types
    import sys

    fake_twilio_rest = types.ModuleType("twilio.rest")
    fake_twilio_rest.Client = FakeClient
    fake_twilio = types.ModuleType("twilio")
    monkeypatch.setitem(sys.modules, "twilio", fake_twilio)
    monkeypatch.setitem(sys.modules, "twilio.rest", fake_twilio_rest)

    sms.send_otp_sms("3155421646", "111222", AuthCodePurpose.LOGIN_2FA)

    assert len(created_messages) == 1
    assert created_messages[0]["to"] == "+573155421646"
    assert created_messages[0]["from_"] == "+15005550006"
    assert "111222" in created_messages[0]["body"]


def test_issue_code_never_raises_when_user_has_a_phone_but_twilio_is_unconfigured(db_session):
    """El flujo real de emitir un código (login 2FA o reset) no debe romperse
    aunque el usuario tenga teléfono y Twilio no esté configurado todavía."""
    from app.models.tenant import Tenant
    from app.models.user import Role, User
    from app.core.security import hash_password
    from app.services.auth_codes import issue_code

    tenant = Tenant(name="Colegio de prueba SMS")
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email="admin-sms@example.com",
        hashed_password=hash_password("Sup3rSegura!"),
        full_name="Admin con teléfono",
        phone="3155421646",
        role=Role.ADMIN,
    )
    db_session.add(user)
    db_session.commit()

    issue_code(db_session, user, AuthCodePurpose.LOGIN_2FA)

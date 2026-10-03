import os

# La URL de la DB de pruebas se fija ANTES de importar app.core.config, para que
# Settings la tome vía variable de entorno y nunca se toquen los datos de desarrollo.
# TEST_DATABASE_URL permite correr las pruebas DENTRO del contenedor de Docker,
# donde la base no está en "localhost" sino en el servicio "db".
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://rutia:rutia_dev_password@localhost:5432/rutia_test"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.main import app
from tests import otp_capture

engine = create_engine(os.environ["DATABASE_URL"])
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recrea el esquema antes de cada test para que los tests queden aislados
    entre sí, incluso cuando el código bajo prueba hace commit() (como los
    endpoints reales)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _capture_otp_codes(monkeypatch):
    """Intercepta el envío de correo de códigos de verificación (2FA de ADMIN
    y recuperación de contraseña) en TODAS las pruebas, para que ninguna
    prueba dependa de credenciales SMTP reales ni mande correos de verdad.
    Los códigos capturados quedan disponibles vía `tests.otp_capture`."""
    otp_capture.SENT_CODES.clear()

    def fake_send_otp_email(to_email, code, purpose):
        otp_capture.SENT_CODES.append((to_email, code, purpose))

    monkeypatch.setattr("app.services.auth_codes.send_otp_email", fake_send_otp_email)
    yield
    otp_capture.SENT_CODES.clear()


@pytest.fixture(autouse=True)
def _block_osrm_network(monkeypatch):
    """Ninguna prueba debe depender del servidor público de OSRM: por defecto
    se simula que no responde (el servicio cae a líneas rectas). Las pruebas
    que necesitan una respuesta de OSRM la simulan explícitamente."""

    def offline_get(*args, **kwargs):
        raise ConnectionError("OSRM deshabilitado en las pruebas")

    monkeypatch.setattr("app.services.routing.httpx.get", offline_get)
    yield


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

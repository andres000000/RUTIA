from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración central de la aplicación, cargada desde variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "RUTIA"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "postgresql+psycopg://rutia:rutia_dev_password@localhost:5432/rutia"

    JWT_SECRET_KEY: str = "change-this-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Clave simple para proteger el endpoint de alta de nuevos colegios (tenants).
    # En este alcance académico no se modela un rol "super-admin" aparte; quien
    # tenga esta clave (el desarrollador/operador de la plataforma) puede dar de
    # alta un colegio nuevo con su primer usuario ADMIN.
    PLATFORM_BOOTSTRAP_KEY: str = "change-this-bootstrap-key"

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:19006",
        "http://localhost:5173",  # servidor de desarrollo del dashboard web (Vite)
    ]

    # --- Seguridad de autenticación (login ADMIN con segundo factor por correo) ---

    # Después de este número de contraseñas incorrectas seguidas, la cuenta se
    # bloquea temporalmente (sin importar que después se ingrese la contraseña
    # correcta) para frenar ataques de fuerza bruta.
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # Cuánto dura vigente un código de 6 dígitos (2FA de login o recuperación
    # de contraseña) y cuántos intentos de adivinarlo se permiten antes de
    # invalidarlo.
    AUTH_CODE_EXPIRE_MINUTES: int = 10
    AUTH_CODE_MAX_ATTEMPTS: int = 5

    # --- Envío de correos (Gmail SMTP con "contraseña de aplicación") ---
    # Si SMTP_USER queda vacío, el backend no intenta enviar correos de verdad:
    # solo registra el código en el log (útil en desarrollo/pruebas sin
    # exponer credenciales reales). En producción/piloto se configuran estas
    # tres variables en el .env.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_APP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "RUTIA"

    # --- Envío de SMS (Twilio) ---
    # Canal adicional al correo para el código de 6 dígitos: se envía por los
    # dos a la vez (ver app/services/auth_codes.py), así que si uno falla o no
    # está configurado, el otro sigue sirviendo como respaldo. Si cualquiera de
    # estas tres variables queda vacía, o el usuario no tiene teléfono
    # registrado, el envío por SMS simplemente se omite (se registra en el log)
    # en vez de fallar el login -- igual que con el correo.
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""


settings = Settings()

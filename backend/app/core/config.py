import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Directorio raíz del código fuente en desarrollo (backend/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def get_user_data_dir() -> Path:
    """
    Resuelve el directorio estándar de configuración y datos persistentes del usuario
    según el sistema operativo (Windows, macOS o Linux).
    - Windows: %APPDATA%/Halibel
    - macOS:   ~/Library/Application Support/Halibel
    - Linux:   $XDG_CONFIG_HOME/Halibel o ~/.config/Halibel
    """
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        base_config = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base_config = Path.home() / "Library" / "Application Support"
    else:
        xdg_config = os.getenv("XDG_CONFIG_HOME")
        base_config = Path(xdg_config) if xdg_config else Path.home() / ".config"

    user_dir = base_config / "Halibel"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


# Directorio persistente en producción (%APPDATA%/Halibel en Windows)
USER_DATA_DIR = get_user_data_dir()

# Candidatos de archivo .env ordenados de MENOR a MAYOR prioridad
# (En pydantic-settings, los archivos posteriores de la tupla sobrescriben a los anteriores)
_ENV_CANDIDATES = (
    USER_DATA_DIR / ".env",  # 1. Respaldo global de producción (%APPDATA%/Halibel/.env)
    BASE_DIR / ".env",       # 2. Directorio del proyecto backend (backend/.env)
    Path.cwd() / ".env",     # 3. Máxima prioridad: directorio de trabajo actual donde se lanzó el proceso
)


def resolve_active_env_path() -> Path:
    """
    Devuelve la ruta del archivo `.env` activo en disco, o la ruta recomendada
    donde el usuario debería crearlo si aún no existe ninguno.
    """
    for candidate in reversed(_ENV_CANDIDATES):
        if candidate.is_file():
            return candidate
    # En binario empaquetado (PyInstaller) recomendamos %APPDATA%/Halibel/.env; en dev, backend/.env
    return USER_DATA_DIR / ".env" if getattr(sys, "frozen", False) else BASE_DIR / ".env"


ACTIVE_ENV_FILE = resolve_active_env_path()


class Settings(BaseSettings):
    """
    Configuración centralizada de Halibel mediante variables de entorno.
    - Qué es: Esquema tipado con Pydantic Settings con resolución multi-entorno (Desarrollo + Producción).
    - Para qué se usa: Cargar variables desde el `.env` local (CWD / backend/) o desde el directorio
      de datos del usuario (%APPDATA%/Halibel/.env) si se ejecuta como ejecutable empaquetado.
    - Qué conecta: Provee configuraciones globales a app/main.py, servicios (llm.py, usage.py) y endpoints.
    """

    PROJECT_NAME: str = "Halibel Runtime"
    VERSION: str = "0.2.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Rutas del sistema expuestas para diagnóstico y persistencia
    USER_DATA_DIR: Path = USER_DATA_DIR
    ACTIVE_ENV_FILE: Path = ACTIVE_ENV_FILE

    # Proveedor de Inteligencia Artificial
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # Directiva base de personalidad (System Prompt)
    HALIBEL_PERSONA: str = (
        "Eres Halibel, una compañera y asistente digital personal inteligente, observadora, serena "
        "y resolutiva. Te comunicas de manera concisa, natural y empática. No eres un chatbot genérico, "
        "sino una presencia que acompaña al usuario en su día a día digital. Responde en el mismo idioma "
        "en el que te hable el usuario (por defecto español)."
    )

    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _ENV_CANDIDATES),
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Instancia singleton accesible en todo el proyecto
settings = Settings()
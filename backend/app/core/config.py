from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Directorio raíz del backend (resuelve rutas de forma segura en Windows y Linux)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    """
    Configuración centralizada de Halibel mediante variables de entorno.
    - Qué es: Esquema tipado con Pydantic Settings.
    - Para qué se usa: Leer y validar variables del archivo backend/.env (API keys, host, puerto, modelo).
    - Qué conecta: Provee configuraciones globales a app/main.py, servicios (llm.py, usage.py) y endpoints.
    """
    PROJECT_NAME: str = "Halibel Runtime"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    
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
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instancia singleton accesible en todo el proyecto
settings = Settings()

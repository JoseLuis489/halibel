from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory for the backend (cross-platform safe)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "Halibel Runtime"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    
    # AI Provider Settings
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    
    # System Instruction / Persona
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

settings = Settings()

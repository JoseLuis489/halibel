from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Comprobación de salud y estado operativo del backend.
    - Qué es: Endpoint HTTP GET de diagnóstico (/api/v1/health).
    - Para qué se usa: Permitir que el script de inicio (run-dev.sh), Electron o herramientas de testing
      verifiquen que el servidor FastAPI está arriba y si la API key está cargada.
    - Qué conecta: Consulta `settings` para retornar el nombre de la app, versión y estado de la API key.
    """
    return {
        "status": "online",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "api_key_configured": bool(settings.GEMINI_API_KEY)
    }

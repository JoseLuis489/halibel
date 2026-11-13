import sys
from pathlib import Path

# Asegurar que el directorio 'backend' esté en sys.path tanto si se ejecuta
# directamente como script o desde la raíz del proyecto.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import chat, health, usage
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend local de Halibel AI Companion"
)

# CORS setup for Electron renderer and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(usage.router, tags=["Usage Monitor"])

@app.get("/usage", include_in_schema=False)
async def redirect_to_usage():
    """
    Atajo amigable para acceder al monitor de uso.
    - Qué es: Redirección HTTP 307 (/usage -> /admin/usage).
    - Para qué se usa: Facilitar la apertura del dashboard desde el navegador sin escribir la ruta completa.
    - Qué conecta: Redirige al endpoint `usage_dashboard`.
    """
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/admin/usage")

@app.get("/")
async def root():
    """
    Punto de entrada base del servidor FastAPI.
    - Qué es: Endpoint raíz informativa (GET /).
    - Para qué se usa: Confirmar que el backend está encendido y ofrecer enlaces rápidos a Swagger y Health.
    - Qué conecta: Muestra el estado del servicio y enlaces a `/docs` y `/api/v1/health`.
    """
    return {
        "message": "Halibel Engine is running.",
        "docs": "/docs",
        "health": "/api/v1/health"
    }

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )

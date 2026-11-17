import logging
import os  # noqa: F401
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Asegurar que el directorio 'backend' esté en sys.path tanto si se ejecuta
# directamente como script o desde la raíz del proyecto.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.endpoints import chat, health, memories, usage
from app.core.config import settings

logger = logging.getLogger(__name__)

# =====================================================================
# CONFIGURACIÓN DE SEGURIDAD LOCAL (CORS + HANDSHAKE SECRET)
# =====================================================================

# Orígenes locales estrictamente autorizados (Vite Dev, Backend local y Electron empaquetado)
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "app://.",
]

# Token efímero inyectado por Electron al levantar el proceso hijo (Sidecar).
# Si se define en las variables de entorno, se exige en cada petición HTTP y conexión WebSocket.
HANDSHAKE_SECRET: str | None = settings.HALIBEL_IPC_SECRET

# Rutas públicas locales que no requieren cabecera de Electron (diagnóstico y dashboard web)
EXEMPT_PATHS = frozenset({
    "/",
    "/api/v1/health",
    "/usage",
    "/admin/usage",
    "/docs",
    "/redoc",
    "/openapi.json",
})


class HandshakeSecretMiddleware(BaseHTTPMiddleware):
    """
    Middleware de verificación de token efímero local (Handshake Secret).
    - Qué es: Barrera de autenticación entre el proceso de Electron y FastAPI.
    - Para qué se usa: Impedir que páginas web maliciosas o scripts externos en la misma máquina
      accedan a `/api/v1/memories` o abran un WebSocket, incluso si evaden CORS.
    - Cómo funciona:
        * En HTTP: verifica la cabecera `X-Halibel-Secret`.
        * En modo desarrollo (`DEBUG=True` sin `HALIBEL_IPC_SECRET`): permite paso para tests/dev.
    """

    async def dispatch(self, request: Request, call_next):
        # Permitir preflight OPTIONS de CORS y rutas exentas (health / dashboard local)
        if request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Si hay secreto activo, validar en tiempo constante (anti timing-attacks)
        if HANDSHAKE_SECRET:
            provided_secret = request.headers.get("X-Halibel-Secret") or request.query_params.get("token")
            if not provided_secret or not secrets.compare_digest(provided_secret, HANDSHAKE_SECRET):
                logger.warning(
                    "Intento de acceso HTTP no autorizado bloqueado en %s desde %s",
                    request.url.path,
                    request.client.host if request.client else "desconocido",
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "No autorizado: Handshake Secret inválido o ausente."},
                )

        return await call_next(request)


# =====================================================================
# CICLO DE VIDA Y APLICACIÓN FASTAPI
# =====================================================================


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Gestiona los eventos de arranque y apagado ordenado del motor de Halibel."""
    if HANDSHAKE_SECRET:
        logger.info("Seguridad local activa: Handshake Secret habilitado para IPC.")
    else:
        logger.warning(
            "Modo desarrollo sin HALIBEL_IPC_SECRET: endpoints abiertos en localhost (DEBUG=True)."
        )
    yield
    logger.info("Apagando Halibel Runtime limpiamente.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend local de Halibel AI Companion",
    # Ocultar documentación interactiva en producción para reducir superficie de exposición
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# 1. Verificación de Handshake Secret (se ejecuta antes de procesar rutas)
app.add_middleware(HandshakeSecretMiddleware)

# 2. CORS estricto: bloquea peticiones cross-origin desde navegadores externos (Chrome, Firefox, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Halibel-Secret"],
)

# =====================================================================
# REGISTRO DE ROUTERS Y ENDPOINTS BASE
# =====================================================================

app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(memories.router, prefix="/api/v1", tags=["Memory & Profile"])
app.include_router(usage.router, tags=["Usage Monitor"])


@app.get("/usage", include_in_schema=False)
async def redirect_to_usage():
    """
    Atajo amigable para acceder al monitor de uso.
    - Qué es: Redirección HTTP 307 (/usage -> /admin/usage).
    - Para qué se usa: Facilitar la apertura del dashboard desde el navegador sin escribir la ruta completa.
    - Qué conecta: Redirige al endpoint `usage_dashboard`.
    """
    return RedirectResponse(url="/admin/usage")


@app.get("/")
async def root():
    """
    Punto de entrada base del servidor FastAPI.
    - Qué es: Endpoint raíz informativa (GET /).
    - Para qué se usa: Confirmar que el backend está encendido y ofrecer enlaces rápidos a Health.
    - Qué conecta: Muestra el estado del servicio y enlaces a `/api/v1/health`.
    """
    return {
        "message": "Halibel Engine is running.",
        "version": settings.VERSION,
        "docs": "/docs" if settings.DEBUG else None,
        "health": "/api/v1/health",
    }


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
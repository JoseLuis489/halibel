import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.memory import memory_service

logger = logging.getLogger(__name__)
router = APIRouter()


class MemoryCreateRequest(BaseModel):
    """Contrato de entrada para crear o actualizar un recuerdo del usuario."""

    category: str = Field(default="preference", description="Categoría semántica del recuerdo.")
    content: str = Field(..., min_length=1, description="Contenido textual del recuerdo.")
    key: str | None = Field(default=None, description="Identificador único opcional para upsert.")
    importance: int = Field(default=1, ge=1, le=5, description="Nivel de importancia (1-5).")


class MemoryResponse(BaseModel):
    """Esquema de respuesta de un recuerdo almacenado."""

    id: int
    category: str
    key: str | None
    content: str
    importance: int
    created_at: str
    updated_at: str


@router.get("/memories", response_model=list[dict[str, Any]])
async def list_memories(
    query: str | None = Query(default=None, description="Filtrar por texto en contenido o clave."),
    category: str | None = Query(default=None, description="Filtrar por categoría."),
    limit: int = Query(default=50, ge=1, le=100, description="Límite de resultados."),
):
    """
    Lista y busca recuerdos en la memoria a largo plazo de Halibel.
    - Qué es: Endpoint REST de consulta de recuerdos (GET /api/v1/memories).
    - Para qué se usa: Permitir a paneles de administración o interfaces inspeccionar lo que Halibel recuerda.
    - Qué conecta: Llama a `memory_service.search_memories`.
    """
    return memory_service.search_memories(query=query, category=category, limit=limit)


@router.post("/memories", response_model=dict[str, Any])
async def create_or_update_memory(req: MemoryCreateRequest):
    """
    Crea o actualiza un recuerdo del usuario en la base de datos persistente.
    - Qué es: Endpoint REST de inserción/actualización de memoria (POST /api/v1/memories).
    - Para qué se usa: Guardar explícitamente preferencias o hechos desde la interfaz.
    - Qué conecta: Llama a `memory_service.save_memory`.
    """
    try:
        saved = memory_service.save_memory(
            category=req.category,
            content=req.content,
            key=req.key,
            importance=req.importance,
        )
        return saved
    except Exception as e:
        logger.exception("Error guardando recuerdo vía REST")
        raise HTTPException(status_code=500, detail=f"Error al guardar memoria: {e!s}") from e


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int):
    """
    Elimina un recuerdo específico por su identificador.
    - Qué es: Endpoint REST de borrado de recuerdo (DELETE /api/v1/memories/{memory_id}).
    - Para qué se usa: Descartar recuerdos obsoletos desde la UI.
    - Qué conecta: Llama a `memory_service.delete_memory`.
    """
    deleted = memory_service.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No se encontró el recuerdo con ID {memory_id}.")
    return {"status": "success", "message": f"Recuerdo {memory_id} eliminado exitosamente."}


@router.get("/chat/history")
async def get_chat_history(
    session_id: str = Query(default="default_session", description="Identificador de la sesión."),
    limit: int = Query(default=50, ge=1, le=100, description="Cantidad máxima de mensajes."),
):
    """
    Recupera el historial cronológico de mensajes de una sesión conversacional.
    - Qué es: Endpoint REST de recuperación de historial (GET /api/v1/chat/history).
    - Para qué se usa: Cargar mensajes previos en el cliente al arrancar o cambiar de chat.
    - Qué conecta: Llama a `memory_service.get_session_messages`.
    """
    return memory_service.get_session_messages(session_id=session_id, limit=limit)


@router.delete("/chat/history")
async def clear_chat_history(
    session_id: str = Query(default="default_session", description="Identificador de la sesión a vaciar."),
):
    """
    Vacía todos los mensajes de una sesión de chat conservando la sesión.
    - Qué es: Endpoint REST de vaciado de conversación (DELETE /api/v1/chat/history).
    - Para qué se usa: Permitir al usuario resetear el historial visual de la conversación.
    - Qué conecta: Llama a `memory_service.clear_session_messages`.
    """
    cleared = memory_service.clear_session_messages(session_id=session_id)
    return {"status": "success", "session_id": session_id, "cleared": cleared}

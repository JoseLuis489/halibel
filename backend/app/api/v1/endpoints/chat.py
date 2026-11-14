import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocketState
from pydantic import BaseModel, Field, ValidationError

from app.services.llm import llm_service

logger = logging.getLogger(__name__)
router = APIRouter()


# =====================================================================
# ESQUEMAS DE VALIDACIÓN (PYDANTIC)
# =====================================================================


class Message(BaseModel):
    """Esquema de un mensaje individual en el historial de conversación."""

    role: Literal["user", "model", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Contrato de entrada para la solicitud de chat HTTP (SSE)."""

    message: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)


class WSIncomingMessage(BaseModel):
    """
    Contrato tipado para mensajes entrantes por WebSocket.
    Preparado para soportar múltiples tipos de eventos en la Fase 2.
    """

    type: Literal["chat", "cancel", "tool_approval_response"] = "chat"
    message: str = ""
    history: list[Message] = Field(default_factory=list)
    # Campos reservados para Fase 2 (aprobación de herramientas):
    tool_call_id: str | None = None
    approved: bool | None = None


# =====================================================================
# ENDPOINT HTTP SSE (SERVER-SENT EVENTS)
# =====================================================================


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Endpoint HTTP Server-Sent Events (SSE) para streaming de tokens.
    - Qué es: Endpoint HTTP POST tradicional con respuesta en flujo continuo (text/event-stream).
    - Para qué se usa: Permitir que clientes HTTP reciban tokens conforme se generan sin WebSockets.
    """
    history_dicts = [{"role": m.role, "content": m.content} for m in request.history]

    async def event_generator():
        try:
            async for chunk in llm_service.stream_message(request.message, history=history_dicts):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.info("Cliente HTTP SSE desconectado durante el streaming.")
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# =====================================================================
# ENDPOINT WEBSOCKET BIDIRECCIONAL
# =====================================================================


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """
    Envía un JSON por WebSocket verificando primero que el socket siga abierto.
    Evita lanzar RuntimeError si el cliente cerró la ventana abruptamente.
    """
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("No se pudo enviar mensaje; el socket ya estaba cerrándose.")
    return False


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    Canal WebSocket bidireccional de baja latencia con el cliente Electron.
    - Qué es: Conexión persistente full-duplex sobre TCP (/api/v1/ws/chat).
    - Para qué se usa:
        1. Recibir y validar mensajes del usuario o comandos de control (cancelación / aprobación).
        2. Ejecutar el streaming del LLM en una tarea asíncrona independiente para no bloquear la lectura.
        3. Cancelar inmediatamente la generación si el usuario aborta o cierra la ventana (ahorro de tokens).
    """
    await websocket.accept()
    logger.info("Cliente conectado al WebSocket de chat.")

    # Referencia a la tarea de streaming activa para poder cancelarla si el cliente se desconecta o aborta
    active_stream_task: asyncio.Task | None = None

    async def run_llm_stream(user_msg: str, history_data: list[dict[str, str]]) -> None:
        """Corrutina aislada que consume el generador de Gemini y envía chunks al cliente."""
        if not await _safe_send_json(websocket, {"type": "stream_start"}):
            return

        try:
            async for chunk in llm_service.stream_message(user_msg, history=history_data):
                sent = await _safe_send_json(websocket, {"type": "chunk", "content": chunk})
                if not sent:
                    # Si falló el envío porque el cliente cayó, cortamos el generador inmediatamente
                    break

            await _safe_send_json(websocket, {"type": "stream_end"})

        except asyncio.CancelledError:
            # Propagación limpia: esto dispara el `except asyncio.CancelledError` dentro de llm_service
            logger.info("Tarea de streaming abortada; deteniendo consumo de tokens.")
            await _safe_send_json(websocket, {"type": "stream_end"})
            raise

        except Exception:
            logger.exception("Error inesperado en la tarea de streaming del WebSocket")
            await _safe_send_json(
                websocket,
                {"type": "error", "error": "Ocurrió un error interno al procesar la respuesta."},
            )

    try:
        while True:
            raw_data = await websocket.receive_text()

            # 1. Validación estricta del payload entrante sin tumbar la conexión
            try:
                payload = WSIncomingMessage.model_validate_json(raw_data)
            except ValidationError as e:
                logger.warning("Mensaje WebSocket con formato inválido recibido: %s", e.errors())
                await _safe_send_json(
                    websocket,
                    {"type": "error", "error": "Formato de mensaje inválido."},
                )
                continue

            # 2. Enrutamiento por tipo de evento (listo para Fase 2)
            if payload.type == "cancel":
                if active_stream_task and not active_stream_task.done():
                    logger.info("Solicitud de cancelación recibida desde el cliente.")
                    active_stream_task.cancel()
                continue

            if payload.type == "tool_approval_response":
                # Placeholder estructurado para la Fase 2
                logger.info(
                    "Respuesta de aprobación de herramienta recibida: id=%s, approved=%s",
                    payload.tool_call_id,
                    payload.approved,
                )
                continue

            if payload.type == "chat":
                if not payload.message.strip():
                    continue

                # Si ya había una respuesta generándose, la cancelamos antes de iniciar la nueva
                if active_stream_task and not active_stream_task.done():
                    active_stream_task.cancel()

                history_dicts = [{"role": m.role, "content": m.content} for m in payload.history]
                active_stream_task = asyncio.create_task(
                    run_llm_stream(payload.message, history_dicts)
                )

    except WebSocketDisconnect:
        logger.info("Cliente desconectado del WebSocket de chat.")
    except Exception:
        logger.exception("Error crítico en el bucle principal del WebSocket")
    finally:
        # GARANTÍA ANTI-FUGA DE TOKENS:
        # Al salir del bucle (por desconexión o error), cancelamos cualquier generación activa en segundo plano
        if active_stream_task and not active_stream_task.done():
            logger.info("Cancelando streaming activo tras desconexión del WebSocket.")
            active_stream_task.cancel()
            try:
                await active_stream_task
            except asyncio.CancelledError:
                pass
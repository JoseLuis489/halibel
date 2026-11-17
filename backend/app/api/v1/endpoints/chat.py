import asyncio
import json
import logging
import os  # noqa: F401
import secrets
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocketState
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.llm import llm_service
from app.services.memory import memory_service

logger = logging.getLogger(__name__)
router = APIRouter()

# =====================================================================
# SEGURIDAD DE CONEXIÓN WEBSOCKET (ANTI-CSWSH + HANDSHAKE SECRET)
# =====================================================================

ALLOWED_WS_ORIGINS = frozenset({
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "app://.",
    "file://",
    "null",
})

# Mismo token efímero inyectado por Electron al levantar el proceso hijo (Sidecar)
HANDSHAKE_SECRET: str | None = settings.HALIBEL_IPC_SECRET


def _is_authorized_websocket(websocket: WebSocket) -> bool:
    """
    Verifica la legitimidad de una solicitud de conexión WebSocket antes de aceptarla.
    - Qué es: Barrera defensiva contra Cross-Site WebSocket Hijacking (CSWSH).
    - Para qué se usa:
        1. Rechazar cualquier navegador externo cuyo encabezado `Origin` no pertenezca a Halibel.
        2. Validar el token efímero `HALIBEL_IPC_SECRET` si está configurado en el entorno.
    - Nota: Si `Origin` es `None` (clientes nativos o TestClient de Pytest), se permite si pasa el token.
    """
    origin = websocket.headers.get("origin")
    if origin is not None:
        # Normalizamos quitando barra final por si algún entorno envía "app://./"
        normalized_origin = origin.rstrip("/")
        allowed_normalized = {o.rstrip("/") for o in ALLOWED_WS_ORIGINS}
        # En Electron empaquetado cargando desde disco, Origin puede llegar como "file://" o "null"
        if normalized_origin not in allowed_normalized and not origin.startswith("file://"):
            logger.warning("Conexión WebSocket rechazada por Origin no autorizado: '%s'", origin)
            return False

    # Si hay secreto IPC activo (producción / Electron), exigir coincidencia exacta en tiempo constante
    if HANDSHAKE_SECRET:
        provided_token = websocket.query_params.get("token") or websocket.headers.get("x-halibel-secret")
        if not provided_token or not secrets.compare_digest(provided_token, HANDSHAKE_SECRET):
            logger.warning("Conexión WebSocket rechazada por Handshake Secret inválido o ausente.")
            return False

    return True


def _clean_response_for_memory(raw_text: str) -> str:
    """
    Elimina las etiquetas efímeras de estado de herramientas antes de persistir en SQLite,
    evitando que el modelo vea sus propios indicadores visuales en el historial futuro.
    """
    status_prefixes = (
        "🔍 *[Consultando:",
        "🧠 *[Actualizando memoria:",
        "⚙️ *[Ejecutando:",
        "⚠️ *[Solicitando autorización",
        "*[Acción autorizada",
        "*[Acción cancelada",
    )
    cleaned_lines = [
        line for line in raw_text.splitlines()
        if not line.strip().startswith(status_prefixes)
    ]
    return "\n".join(cleaned_lines).strip()


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
    Soporta eventos de chat, cancelación, aprobación interactiva y sincronización de historial.
    """

    type: Literal[
        "chat",
        "cancel",
        "tool_approval_response",
        "get_history",
        "clear_history",
    ] = "chat"
    message: str = ""
    session_id: str = "default_session"
    history: list[Message] = Field(default_factory=list)
    # Campos para autorización interactiva (Human-in-the-Loop):
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
        1. Validar Origin y Handshake Secret antes de aceptar el handshake WebSocket (anti-CSWSH).
        2. Recibir y validar mensajes del usuario o comandos de control (cancelación / aprobación).
        3. Ejecutar el streaming del LLM en una tarea asíncrona independiente para no bloquear la lectura.
        4. Cancelar inmediatamente la generación si el usuario aborta o cierra la ventana (ahorro de tokens).
    """
    # BARRERA ANTI-CSWSH: Validar origen y secreto ANTES de aceptar el socket
    if not _is_authorized_websocket(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin or token not authorized")
        return

    await websocket.accept()
    logger.info("Cliente autorizado conectado al WebSocket de chat.")

    # Referencia a la tarea de streaming activa para poder cancelarla si el cliente se desconecta o aborta
    active_stream_task: asyncio.Task | None = None

    # Diccionario de promesas asíncronas pendientes de aprobación humana (Human-in-the-Loop)
    pending_approvals: dict[str, asyncio.Future[bool]] = {}

    async def on_approval_request(call_id: str, tool_name: str, args: dict[str, Any], description: str) -> bool:
        """
        Solicita autorización interactiva al usuario a través del canal WebSocket.
        - Qué es: Callback asíncrono para herramientas destructivas (Human-in-the-Loop).
        - Para qué se usa: Enviar la solicitud de confirmación a la UI de Electron y suspender la ejecución
          hasta que el usuario responda o se alcance el tiempo de espera preventivo (timeout).
        - Qué conecta: Conecta el bucle de herramientas en `llm_service` con el frontend React.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        pending_approvals[call_id] = future

        sent = await _safe_send_json(
            websocket,
            {
                "type": "tool_approval_request",
                "tool_call_id": call_id,
                "tool": tool_name,
                "args": args,
                "description": description,
            },
        )
        if not sent:
            pending_approvals.pop(call_id, None)
            return False

        try:
            # Espera máxima de 120 segundos para que el usuario responda en la interfaz
            return await asyncio.wait_for(future, timeout=120.0)
        except TimeoutError:
            logger.warning("Tiempo de espera agotado (120s) para la aprobación de herramienta: %s", call_id)
            return False
        finally:
            pending_approvals.pop(call_id, None)

    async def run_llm_stream(
        user_msg: str,
        history_data: list[dict[str, str]],
        session_id: str = "default_session",
    ) -> None:
        """Corrutina aislada que consume el generador de Gemini, envía chunks y persiste el diálogo."""
        if not await _safe_send_json(websocket, {"type": "stream_start"}):
            return

        accumulated_chunks: list[str] = []
        try:
            async for chunk in llm_service.stream_message(
                user_msg,
                history=history_data,
                on_approval_request=on_approval_request,
            ):
                accumulated_chunks.append(chunk)
                sent = await _safe_send_json(websocket, {"type": "chunk", "content": chunk})
                if not sent:
                    # Si falló el envío porque el cliente cayó, cortamos el generador inmediatamente
                    break

            await _safe_send_json(websocket, {"type": "stream_end"})

            # Persistencia limpia de la respuesta del asistente en SQLite local sin bloquear asyncio
            full_response = _clean_response_for_memory("".join(accumulated_chunks))
            if full_response:
                await asyncio.to_thread(
                    memory_service.save_message,
                    session_id=session_id,
                    role="model",
                    content=full_response,
                )

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

            # 2. Enrutamiento por tipo de evento
            if payload.type == "get_history":
                msgs = await asyncio.to_thread(
                    memory_service.get_session_messages, payload.session_id, 50
                )
                await _safe_send_json(websocket, {"type": "history", "messages": msgs})
                continue

            if payload.type == "clear_history":
                await asyncio.to_thread(
                    memory_service.clear_session_messages, payload.session_id
                )
                await _safe_send_json(websocket, {"type": "history_cleared"})
                continue

            if payload.type == "cancel":
                if active_stream_task and not active_stream_task.done():
                    logger.info("Solicitud de cancelación recibida desde el cliente.")
                    active_stream_task.cancel()
                continue

            if payload.type == "tool_approval_response":
                logger.info(
                    "Respuesta de aprobación de herramienta recibida: id=%s, approved=%s",
                    payload.tool_call_id,
                    payload.approved,
                )
                if payload.tool_call_id and payload.tool_call_id in pending_approvals:
                    fut = pending_approvals[payload.tool_call_id]
                    if not fut.done():
                        fut.set_result(bool(payload.approved))
                continue

            if payload.type == "chat":
                if not payload.message.strip():
                    continue

                # Si ya había una respuesta generándose, la cancelamos antes de iniciar la nueva
                if active_stream_task and not active_stream_task.done():
                    active_stream_task.cancel()

                # Persistir mensaje del usuario en la base de datos local sin bloquear el loop
                await asyncio.to_thread(
                    memory_service.save_message,
                    session_id=payload.session_id,
                    role="user",
                    content=payload.message,
                )

                history_dicts = [{"role": m.role, "content": m.content} for m in payload.history]
                active_stream_task = asyncio.create_task(
                    run_llm_stream(payload.message, history_dicts, payload.session_id)
                )

    except WebSocketDisconnect:
        logger.info("Cliente desconectado del WebSocket de chat.")
    except Exception:
        logger.exception("Error crítico en el bucle principal del WebSocket")
    finally:
        # Cancelar cualquier autorización interactiva que haya quedado colgada
        for fut in pending_approvals.values():
            if not fut.done():
                fut.cancel()
        pending_approvals.clear()

        # GARANTÍA ANTI-FUGA DE TOKENS:
        # Al salir del bucle (por desconexión o error), cancelamos cualquier generación activa en segundo plano
        if active_stream_task and not active_stream_task.done():
            logger.info("Cancelando streaming activo tras desconexión del WebSocket.")
            active_stream_task.cancel()
            try:
                await active_stream_task
            except asyncio.CancelledError:
                pass
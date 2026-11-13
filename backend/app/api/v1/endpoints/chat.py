import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.llm import llm_service

router = APIRouter()

class Message(BaseModel):
    """Esquema de un mensaje individual en el historial de conversación."""
    role: str
    content: str

class ChatRequest(BaseModel):
    """Contrato de entrada para la solicitud de chat HTTP."""
    message: str
    history: list[Message] | None = []

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Endpoint HTTP Server-Sent Events (SSE) para streaming de tokens.
    - Qué es: Endpoint HTTP POST tradicional con respuesta en flujo continuo (text/event-stream).
    - Para qué se usa: Permitir que clientes HTTP o navegadores reciban tokens conforme se generan,
      sin requerir WebSockets.
    - Qué conecta: Recibe `ChatRequest` y consume el generador de `llm_service.stream_message`.
    """
    history_dicts = [{"role": m.role, "content": m.content} for m in (request.history or [])]
    
    async def event_generator():
        async for chunk in llm_service.stream_message(request.message, history=history_dicts):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    Canal WebSocket bidireccional de baja latencia con el cliente Electron.
    - Qué es: Conexión persistente full-duplex sobre TCP (/api/v1/ws/chat).
    - Para qué se usa:
        1. Recibir mensajes del usuario enviados desde la interfaz de escritorio.
        2. Emitir eventos de ciclo de vida (`stream_start`, `stream_end`).
        3. Transmitir los fragmentos de texto (`chunk`) en tiempo real hacia la UI de Halibel.
        4. Notificar errores de conexión o sobrecarga de la API.
    - Qué conecta: Conecta directamente el componente `App.tsx` en Electron con `llm_service.stream_message`.
    """
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            user_message = payload.get("message", "")
            history = payload.get("history", [])

            if not user_message.strip():
                continue

            # Emitir evento de inicio de respuesta
            await websocket.send_json({"type": "stream_start"})

            # Transmitir fragmentos generados por el modelo
            async for chunk in llm_service.stream_message(user_message, history=history):
                await websocket.send_json({"type": "chunk", "content": chunk})

            # Emitir evento de finalización
            await websocket.send_json({"type": "stream_end"})

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await websocket.send_json({"type": "error", "error": str(e)})

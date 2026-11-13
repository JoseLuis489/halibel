import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.llm import llm_service

router = APIRouter()

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[Message] | None = []

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Endpoint HTTP Server-Sent Events (SSE) para recibir tokens en tiempo real.
    """
    history_dicts = [{"role": m.role, "content": m.content} for m in (request.history or [])]
    
    async def event_generator():
        async for chunk in llm_service.stream_message(request.message, history=history_dicts):
            # Send each text chunk wrapped in SSE format
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket bidireccional para el cliente Electron.
    Permite enviar mensajes y recibir streaming instantáneo de tokens.
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

            # Emit start event
            await websocket.send_json({"type": "stream_start"})

            # Stream response chunks
            async for chunk in llm_service.stream_message(user_message, history=history):
                await websocket.send_json({"type": "chunk", "content": chunk})

            # Emit end event
            await websocket.send_json({"type": "stream_end"})

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        await websocket.send_json({"type": "error", "error": str(e)})

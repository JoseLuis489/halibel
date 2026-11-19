from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

def test_chat_schema():
    # Enviar un mensaje sin API key configurada debe retornar un mensaje amigable o streaming
    response = client.post("/api/v1/chat/stream", json={"message": "Hola"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

def test_websocket_cancel_and_approval():
    with client.websocket_connect("/api/v1/ws/chat") as websocket:
        # Enviar cancelación sin streaming activo no debe romper el servidor
        websocket.send_json({"type": "cancel"})
        
        # Enviar respuesta de aprobación no esperada no debe romper el servidor
        websocket.send_json({
            "type": "tool_approval_response",
            "tool_call_id": "call_test_123",
            "approved": True
        })
        
        # Enviar JSON malformado debe recibir un mensaje de error sin cerrar el socket
        websocket.send_text("esto-no-es-json")
        err_msg = websocket.receive_json()
        assert err_msg["type"] == "error"
        assert "inválido" in err_msg["error"]


def test_websocket_history_sync():
    """Prueba la sincronización de historial a través del canal WebSocket."""
    with client.websocket_connect("/api/v1/ws/chat") as websocket:
        # Solicitar historial
        websocket.send_json({"type": "get_history", "session_id": "test_ws_session"})
        resp = websocket.receive_json()
        assert resp["type"] == "history"
        assert isinstance(resp["messages"], list)

        # Vaciar historial
        websocket.send_json({"type": "clear_history", "session_id": "test_ws_session"})
        ack = websocket.receive_json()
        assert ack["type"] == "history_cleared"


def test_memories_rest_api():
    """Prueba los endpoints REST de consulta y guardado de memorias a largo plazo."""
    # 1. Crear memoria vía POST
    post_res = client.post(
        "/api/v1/memories",
        json={
            "category": "preference",
            "content": "Prefiere té verde sobre café",
            "key": "drink_preference",
            "importance": 2,
        },
    )
    assert post_res.status_code == 200
    data = post_res.json()
    assert data["key"] == "drink_preference"
    mem_id = data["id"]

    # 2. Consultar memoria vía GET
    get_res = client.get("/api/v1/memories", params={"query": "té verde"})
    assert get_res.status_code == 200
    mems = get_res.json()
    assert len(mems) >= 1
    assert any(m["key"] == "drink_preference" for m in mems)

    # 3. Eliminar memoria vía DELETE
    del_res = client.delete(f"/api/v1/memories/{mem_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

    # 4. Intentar eliminar id inexistente -> 404
    del_404 = client.delete("/api/v1/memories/999999")
    assert del_404.status_code == 404


def test_chat_history_rest_api():
    """Prueba los endpoints REST de historial de chat."""
    session_id = "rest_test_session"
    # Consultar historial
    res_get = client.get("/api/v1/chat/history", params={"session_id": session_id})
    assert res_get.status_code == 200
    assert isinstance(res_get.json(), list)

    # Vaciar historial
    res_del = client.delete("/api/v1/chat/history", params={"session_id": session_id})
    assert res_del.status_code == 200
    assert res_del.json()["cleared"] is True


def test_websocket_chat_with_image_data(monkeypatch):
    """Verifica que el canal WebSocket reciba y procese mensajes multimodales con imagen."""
    from app.services.llm import llm_service

    # Mock del generador asíncrono de llm_service
    async def mock_stream(*args, **kwargs):
        assert kwargs.get("image_data") is not None
        yield "He analizado la captura de tu pantalla y veo el código correctamente."

    monkeypatch.setattr(llm_service, "stream_message", mock_stream)

    tiny_png_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    with client.websocket_connect("/api/v1/ws/chat") as websocket:
        websocket.send_json({
            "type": "chat",
            "message": "¿Qué ves en esta pantalla?",
            "session_id": "test_multimodal",
            "image_data": tiny_png_b64,
        })

        # Primer evento debe ser stream_start
        msg1 = websocket.receive_json()
        assert msg1["type"] == "stream_start"

        # Segundo evento es el chunk de respuesta
        msg2 = websocket.receive_json()
        assert msg2["type"] == "chunk"
        assert "captura de tu pantalla" in msg2["content"]

        # Tercer evento es stream_end
        msg3 = websocket.receive_json()
        assert msg3["type"] == "stream_end"


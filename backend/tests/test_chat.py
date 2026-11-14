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


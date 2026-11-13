from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_chat_schema():
    # Enviar un mensaje sin API key configurada debe retornar un mensaje amigable o streaming
    response = client.post("/api/v1/chat/stream", json={"message": "Hola"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

from app.main import app
from app.services.usage import usage_service
from fastapi.testclient import TestClient

client = TestClient(app)

def test_usage_service_and_endpoints():
    # Registrar un uso de prueba
    initial_stats = usage_service.get_stats()
    initial_requests = initial_stats["total_requests"]

    cost = usage_service.log_usage("gemini-3.5-flash-lite", 1000, 500)
    assert cost > 0

    # Comprobar endpoint JSON
    response = client.get("/api/v1/usage/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_requests"] == initial_requests + 1
    assert data["total_prompt_tokens"] >= 1000
    assert data["total_candidates_tokens"] >= 500

    # Comprobar endpoint HTML del dashboard
    html_response = client.get("/admin/usage")
    assert html_response.status_code == 200
    assert "Halibel — AI Usage Monitor" in html_response.text
    assert "Tokens de entrada" in html_response.text
    assert "Costo acumulado" in html_response.text

def test_usage_redirect():
    response = client.get("/usage", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/usage"

import concurrent.futures
from pathlib import Path

import pytest
from app.services.memory import (
    MAX_MEMORY_CONTENT_CHARS,
    MAX_PROFILE_CONTEXT_CHARS,
    MemoryService,
)
from app.services.usage import UsageService


@pytest.fixture
def memory_svc(tmp_path: Path) -> MemoryService:
    test_db = tmp_path / "test_sec_memory.db"
    return MemoryService(db_path=test_db)


@pytest.fixture
def usage_svc(tmp_path: Path) -> UsageService:
    test_db = tmp_path / "test_sec_usage.db"
    return UsageService(db_path=test_db)


# =====================================================================
# 1. PRUEBAS CONTRA INYECCIÓN SQL (SQL INJECTION RESILIENCE)
# =====================================================================

SQL_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE user_memories; --",
    "'; DROP TABLE usage_logs; --",
    "' UNION SELECT 1, 'admin', 'hacked', 5, '2026', '2026' --",
    "admin' --",
    "\" OR \"\"=\"",
    "1; ATTACH DATABASE '/tmp/pwn.db' AS pwn; --",
]


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_injection_in_memory_service(memory_svc: MemoryService, payload: str):
    """Verifica que ningún payload de inyección SQL altere la lógica o rompa la base de datos."""
    # Test en save_memory con payloads en category, key y content
    mem = memory_svc.save_memory(
        category=payload,
        key=f"sec_key_{payload[:10]}",
        content=f"Payload test: {payload}",
        importance=3,
    )
    assert mem["id"] is not None

    # Test en search_memories (LIKE clause)
    results = memory_svc.search_memories(query=payload, category=payload)
    assert isinstance(results, list)

    # Test en delete_memory_by_key
    memory_svc.delete_memory_by_key(f"sec_key_{payload[:10]}")

    # Verificar que la tabla no fue borrada y sigue operativa
    mem_after = memory_svc.save_memory("test", "Aún funcionando", key="still_alive")
    assert mem_after["id"] is not None


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_injection_in_chat_history(memory_svc: MemoryService, payload: str):
    """Verifica que payloads en session_id y content no ejecuten SQL arbitrario."""
    msg = memory_svc.save_message(
        session_id=payload,
        role="user",
        content=f"Mensaje seguro con {payload}",
    )
    assert msg["id"] is not None

    # Recuperar mensajes con session_id malicioso
    msgs = memory_svc.get_session_messages(session_id=payload, limit=10)
    assert len(msgs) == 1
    assert msgs[0]["content"] == f"Mensaje seguro con {payload}"

    # Limpiar mensajes
    assert memory_svc.clear_session_messages(session_id=payload) is True


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
def test_sql_injection_in_usage_service(usage_svc: UsageService, payload: str):
    """Verifica que nombres de modelo maliciosos no puedan inyectar comandos SQL."""
    cost = usage_svc.log_usage(model=payload, prompt_tokens=100, candidates_tokens=50)
    assert cost > 0

    stats = usage_svc.get_stats()
    assert stats["total_requests"] >= 1


# =====================================================================
# 2. PRUEBAS DE LÍMITES Y BUFFER / OVERFLOW (DATA TAMPERING & DOS)
# =====================================================================


def test_memory_content_truncation_prevent_token_overflow(memory_svc: MemoryService):
    """Un contenido masivo (ej: 10,000 caracteres) debe ser truncado a MAX_MEMORY_CONTENT_CHARS."""
    huge_content = "A" * 10_000
    mem = memory_svc.save_memory("flood", huge_content, key="flood_test")

    assert len(mem["content"]) <= MAX_MEMORY_CONTENT_CHARS
    assert mem["content"].endswith("...")


def test_memory_empty_content_rejected(memory_svc: MemoryService):
    """Contenido vacío o solo espacios debe ser rechazado con ValueError."""
    with pytest.raises(ValueError, match="El contenido del recuerdo no puede estar vacío"):
        memory_svc.save_memory("test", "   ", key="empty_test")


def test_profile_context_hard_cap(memory_svc: MemoryService):
    """El contexto del perfil no debe exceder MAX_PROFILE_CONTEXT_CHARS aunque haya cientos de recuerdos."""
    for i in range(50):
        memory_svc.save_memory("test", f"Recuerdo número {i} con información detallada " * 5, key=f"key_{i}")

    context = memory_svc.get_profile_context(max_items=50)
    assert len(context) <= MAX_PROFILE_CONTEXT_CHARS + 200  # Margen de cabecera


def test_extreme_limits_and_pagination(memory_svc: MemoryService):
    """Valores extremos o negativos de limit en búsqueda no deben causar errores ni SQL exceptions."""
    memory_svc.save_memory("cat", "item 1", key="k1")
    memory_svc.save_memory("cat", "item 2", key="k2")

    # Limit negativo o cero debe ser normalizado internamente (clamp entre 1 y 100)
    res_neg = memory_svc.search_memories(query="item", limit=-5)
    assert len(res_neg) >= 1

    res_huge = memory_svc.search_memories(query="item", limit=999_999)
    assert len(res_huge) <= 100


# =====================================================================
# 3. PRUEBAS DE CONCURRENCIA EXTREMA (RACE CONDITIONS & LOCKS)
# =====================================================================


def test_concurrent_writes_usage_service(usage_svc: UsageService):
    """30 hilos escribiendo simultáneamente no deben disparar 'database is locked'."""
    def worker(i: int):
        usage_svc.log_usage(
            model="gemini-3.8-flash",
            prompt_tokens=100 + i,
            candidates_tokens=50 + i,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(30)]
        for f in concurrent.futures.as_completed(futures):
            f.result()  # Si hubo excepción, la levantará aquí

    stats = usage_svc.get_stats()
    assert stats["total_requests"] == 30


def test_concurrent_writes_memory_service(memory_svc: MemoryService):
    """30 hilos guardando mensajes simultáneamente en SQLite en modo WAL."""
    def worker(i: int):
        memory_svc.save_message(
            session_id=f"session_{i % 3}",
            role="user" if i % 2 == 0 else "model",
            content=f"Mensaje concurrente {i}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(30)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verificar que todos los mensajes se guardaron sin pérdida
    total_msgs = sum(len(memory_svc.get_session_messages(f"session_{s}")) for s in range(3))
    assert total_msgs == 30


# =====================================================================
# 4. PRUEBAS DE SEGURIDAD IPC Y ANTI-CSWSH (HANDSHAKE SECRET)
# =====================================================================


def test_handshake_secret_blocks_unauthorized_websocket(monkeypatch: pytest.MonkeyPatch):
    """Si HALIBEL_IPC_SECRET está activo, rechaza conexiones sin token o con token erróneo."""
    import app.api.v1.endpoints.chat as chat_endpoint
    from app.main import app
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    test_secret = "super-secret-token-12345"
    monkeypatch.setattr(chat_endpoint, "HANDSHAKE_SECRET", test_secret)

    client = TestClient(app)

    # 1. Conexión sin token -> debe ser rechazada con código 1008 (Policy Violation)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: SIM117
        with client.websocket_connect("/api/v1/ws/chat"):
            pass
    assert exc.value.code == 1008

    # 2. Conexión con token inválido -> rechazada
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: SIM117
        with client.websocket_connect("/api/v1/ws/chat?token=wrong-token"):
            pass
    assert exc.value.code == 1008


def test_handshake_secret_allows_authorized_websocket(monkeypatch: pytest.MonkeyPatch):
    """Si se provee el token correcto vía query param o header, autoriza la conexión."""
    import app.api.v1.endpoints.chat as chat_endpoint
    from app.main import app
    from fastapi.testclient import TestClient

    test_secret = "super-secret-token-12345"
    monkeypatch.setattr(chat_endpoint, "HANDSHAKE_SECRET", test_secret)

    client = TestClient(app)

    # Conectar exitosamente con token en query param
    with client.websocket_connect(f"/api/v1/ws/chat?token={test_secret}") as ws:
        ws.send_json({"type": "get_history", "session_id": "auth_test"})
        resp = ws.receive_json()
        assert resp["type"] == "history"


def test_handshake_secret_http_middleware(monkeypatch: pytest.MonkeyPatch):
    """Verifica que el middleware HTTP exija X-Halibel-Secret en rutas protegidas."""
    import app.main as main_module
    from app.main import app
    from fastapi.testclient import TestClient

    test_secret = "super-secret-token-12345"
    monkeypatch.setattr(main_module, "HANDSHAKE_SECRET", test_secret)

    client = TestClient(app)

    # 1. Petición sin cabecera a ruta protegida -> 401 Unauthorized
    res_unauth = client.get("/api/v1/memories")
    assert res_unauth.status_code == 401
    assert "No autorizado" in res_unauth.json()["detail"]

    # 2. Petición con cabecera correcta -> 200 OK
    res_auth = client.get("/api/v1/memories", headers={"X-Halibel-Secret": test_secret})
    assert res_auth.status_code == 200

    # 3. Ruta exenta (Healthcheck) -> 200 OK sin necesidad de secreto
    res_health = client.get("/api/v1/health")
    assert res_health.status_code == 200


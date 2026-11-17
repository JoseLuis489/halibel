from pathlib import Path

import pytest
from app.services.memory import MemoryService


@pytest.fixture
def memory_svc(tmp_path: Path) -> MemoryService:
    """Instancia de MemoryService con base de datos temporal en disco."""
    test_db = tmp_path / "test_memory.db"
    return MemoryService(db_path=test_db)


def test_save_and_get_memory(memory_svc: MemoryService):
    # Guardar recuerdo con clave
    mem1 = memory_svc.save_memory(
        category="preference",
        content="El usuario prefiere respuestas concisas en español.",
        key="response_style",
        importance=2,
    )
    assert mem1["id"] is not None
    assert mem1["category"] == "preference"
    assert mem1["key"] == "response_style"
    assert mem1["content"] == "El usuario prefiere respuestas concisas en español."
    assert mem1["importance"] == 2

    # Recuperar por clave
    fetched = memory_svc.get_memory_by_key("response_style")
    assert fetched is not None
    assert fetched["id"] == mem1["id"]
    assert fetched["content"] == mem1["content"]


def test_memory_upsert_on_conflict(memory_svc: MemoryService):
    # Primer registro
    mem1 = memory_svc.save_memory(
        category="fact",
        content="El usuario se llama Gustavo.",
        key="user_name",
        importance=1,
    )

    # Actualización con la misma clave (debe sobrescribir sin duplicar fila)
    mem2 = memory_svc.save_memory(
        category="fact",
        content="El usuario se llama Gustavo Adolfo.",
        key="user_name",
        importance=3,
    )
    assert mem2["id"] == mem1["id"]
    assert mem2["content"] == "El usuario se llama Gustavo Adolfo."
    assert mem2["importance"] == 3

    # Verificar que solo existe 1 registro en la categoría
    all_facts = memory_svc.get_all_memories(category="fact")
    assert len(all_facts) == 1
    assert all_facts[0]["content"] == "El usuario se llama Gustavo Adolfo."


def test_search_memories(memory_svc: MemoryService):
    memory_svc.save_memory("preference", "Usa modo oscuro en la terminal", key="theme")
    memory_svc.save_memory("project", "Desarrollando la app Halibel en Python y Electron", key="active_project")
    memory_svc.save_memory("project", "Proyecto secundario con FastAPI", key="secondary_project")
    memory_svc.save_memory("fact", "Vive en Lima, Perú")

    # Búsqueda por query
    results = memory_svc.search_memories(query="Halibel")
    assert len(results) == 1
    assert results[0]["key"] == "active_project"

    # Búsqueda por categoría
    proj_results = memory_svc.search_memories(category="project")
    assert len(proj_results) == 2

    # Búsqueda combinada
    combined = memory_svc.search_memories(query="FastAPI", category="project")
    assert len(combined) == 1
    assert combined[0]["key"] == "secondary_project"


def test_delete_memories(memory_svc: MemoryService):
    memory_svc.save_memory("misc", "Dato temporal", key="temp_key")
    assert memory_svc.get_memory_by_key("temp_key") is not None

    # Borrado por clave
    deleted = memory_svc.delete_memory_by_key("temp_key")
    assert deleted is True
    assert memory_svc.get_memory_by_key("temp_key") is None

    # Borrado por ID
    m2 = memory_svc.save_memory("misc", "Dato sin clave")
    deleted2 = memory_svc.delete_memory(m2["id"])
    assert deleted2 is True
    assert memory_svc.delete_memory(9999) is False


def test_get_profile_context(memory_svc: MemoryService):
    # Sin recuerdos -> texto vacío
    assert memory_svc.get_profile_context() == ""

    # Con recuerdos
    memory_svc.save_memory("identity", "Usuario: Gustavo", key="user_name", importance=5)
    memory_svc.save_memory("preference", "Prefiere explicaciones directas", importance=3)

    context = memory_svc.get_profile_context()
    assert "[Perfil y Recuerdos del Usuario]:" in context
    assert "(user_name) Usuario: Gustavo" in context
    assert "Prefiere explicaciones directas" in context


def test_chat_sessions_and_messages(memory_svc: MemoryService):
    session_id = "session_test_1"

    # Guardar mensajes
    msg1 = memory_svc.save_message(
        session_id=session_id,
        role="user",
        content="Hola Halibel",
    )
    assert msg1["id"] is not None
    assert msg1["role"] == "user"
    assert msg1["content"] == "Hola Halibel"

    msg2 = memory_svc.save_message(
        session_id=session_id,
        role="model",
        content="Hola Gustavo, ¿en qué te puedo ayudar hoy?",
        metadata={"tokens": 42},
    )
    assert msg2["metadata"] == {"tokens": 42}

    # Recuperar mensajes en orden cronológico
    messages = memory_svc.get_session_messages(session_id)
    assert len(messages) == 2
    assert messages[0]["content"] == "Hola Halibel"
    assert messages[1]["content"] == "Hola Gustavo, ¿en qué te puedo ayudar hoy?"
    assert messages[1]["metadata"] == {"tokens": 42}

    # Listar sesiones
    sessions = memory_svc.list_sessions()
    assert len(sessions) >= 1
    assert any(s["id"] == session_id for s in sessions)

    # Limpiar mensajes
    assert memory_svc.clear_session_messages(session_id) is True
    assert len(memory_svc.get_session_messages(session_id)) == 0

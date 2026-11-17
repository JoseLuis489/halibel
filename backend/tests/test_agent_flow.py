from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services.llm import LLMService
from app.tools.registry import is_destructive_tool
from google.genai import types


@pytest.mark.anyio
async def test_agent_tool_policy_routing():
    # Verificar que las políticas de herramientas identifican correctamente las destructivas
    assert not is_destructive_tool("list_directory")
    assert not is_destructive_tool("read_file")
    assert is_destructive_tool("safe_delete_file")

@pytest.mark.anyio
async def test_agent_loop_rejection():
    # Simular una llamada a safe_delete_file donde el callback de aprobación devuelve False
    service = LLMService()
    
    # Creamos objetos reales de types.Part y types.FunctionCall
    real_call = types.FunctionCall(
        id="call_mock_1",
        name="safe_delete_file",
        args={"path": "test_protected.txt"}
    )
    real_part = types.Part(function_call=real_call)
    
    mock_chunk_1 = MagicMock()
    mock_chunk_1.usage_metadata = None
    mock_chunk_1.function_calls = [real_call]
    mock_chunk_1.candidates = [
        MagicMock(content=MagicMock(parts=[real_part]))
    ]
    mock_chunk_1.text = None
    
    # Turno 2: modelo responde después del rechazo
    mock_chunk_2 = MagicMock()
    mock_chunk_2.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=15)
    mock_chunk_2.function_calls = None
    mock_chunk_2.candidates = None
    mock_chunk_2.text = "Entendido, no he eliminado el archivo."

    calls_count = 0
    async def mock_generate_stream(*args, **kwargs):
        async def _gen():
            nonlocal calls_count
            calls_count += 1
            if calls_count == 1:
                yield mock_chunk_1
            else:
                yield mock_chunk_2
        return _gen()

    approval_mock = AsyncMock(return_value=False)
    
    mock_client = MagicMock()
    mock_client.aio.models.generate_content_stream = mock_generate_stream
    service._client = mock_client
    
    chunks = []
    async for chunk in service.stream_message(
        "Por favor elimina test_protected.txt",
        on_approval_request=approval_mock
    ):
        chunks.append(chunk)

    # Verificar que se solicitó aprobación al callback
    assert approval_mock.await_count == 1
    call_args = approval_mock.call_args[0]
    assert call_args[0] == "call_mock_1"
    assert call_args[1] == "safe_delete_file"
    assert call_args[2] == {"path": "test_protected.txt"}
    
    # Verificar que el chunk final fue entregado
    full_output = "".join(chunks)
    assert "cancelada o rechazada" in full_output
    assert "Entendido, no he eliminado el archivo." in full_output


@pytest.mark.anyio
async def test_agent_loop_approval(tmp_path):
    # Simular una llamada a safe_delete_file donde el callback devuelve True (aprobado)
    service = LLMService()
    
    # Crear archivo real temporal
    target_file = tmp_path / "temp_to_delete.txt"
    target_file.write_text("archivo para eliminar")
    assert target_file.exists()

    real_call = types.FunctionCall(
        id="call_mock_2",
        name="safe_delete_file",
        args={"path": str(target_file)}
    )
    real_part = types.Part(function_call=real_call)

    mock_chunk_1 = MagicMock()
    mock_chunk_1.usage_metadata = None
    mock_chunk_1.function_calls = [real_call]
    mock_chunk_1.candidates = [
        MagicMock(content=MagicMock(parts=[real_part]))
    ]
    mock_chunk_1.text = None

    mock_chunk_2 = MagicMock()
    mock_chunk_2.usage_metadata = MagicMock(prompt_token_count=12, candidates_token_count=20)
    mock_chunk_2.function_calls = None
    mock_chunk_2.candidates = None
    mock_chunk_2.text = "El archivo fue enviado a la papelera con éxito."

    calls_count = 0
    async def mock_generate_stream(*args, **kwargs):
        async def _gen():
            nonlocal calls_count
            calls_count += 1
            if calls_count == 1:
                yield mock_chunk_1
            else:
                yield mock_chunk_2
        return _gen()

    approval_mock = AsyncMock(return_value=True)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content_stream = mock_generate_stream
    service._client = mock_client

    chunks = []
    async for chunk in service.stream_message(
        f"Elimina {target_file}",
        on_approval_request=approval_mock
    ):
        chunks.append(chunk)

    assert approval_mock.await_count == 1
    assert not target_file.exists()
    full_output = "".join(chunks)
    assert "Acción autorizada por el usuario" in full_output
    assert "El archivo fue enviado a la papelera con éxito." in full_output


@pytest.mark.anyio
async def test_agent_runtime_context_profile_injection(monkeypatch, tmp_path):
    """Prueba que los recuerdos guardados se inyecten dinámicamente en el contexto del agente."""
    import app.services.llm as llm_module
    from app.services.llm import _build_runtime_context
    from app.services.memory import MemoryService

    isolated_db = tmp_path / "test_context_mem.db"
    test_mem_svc = MemoryService(db_path=isolated_db)
    monkeypatch.setattr(llm_module, "memory_service", test_mem_svc)

    # 1. Sin memorias
    ctx_empty = _build_runtime_context()
    assert "[Contexto del sistema del usuario]:" in ctx_empty
    assert "[Perfil y Recuerdos del Usuario]:" not in ctx_empty

    # 2. Con memorias registradas
    test_mem_svc.save_memory("identity", "El usuario es desarrollador de software.", key="user_role", importance=4)
    test_mem_svc.save_memory("preference", "Prefiere explicaciones en español.", key="lang_pref", importance=5)

    ctx_with_mem = _build_runtime_context()
    assert "[Perfil y Recuerdos del Usuario]:" in ctx_with_mem
    assert "(user_role) El usuario es desarrollador de software." in ctx_with_mem
    assert "(lang_pref) Prefiere explicaciones en español." in ctx_with_mem


@pytest.mark.anyio
async def test_agent_loop_write_file_safe(tmp_path):
    """Prueba que el agente ejecute write_file directamente cuando es un archivo nuevo sin bloquearse."""
    service = LLMService()
    new_doc = tmp_path / "nuevo_documento.txt"

    real_call = types.FunctionCall(
        id="call_mock_write_1",
        name="write_file",
        args={"path": str(new_doc), "content": "Contenido generado por Halibel", "overwrite": False}
    )
    real_part = types.Part(function_call=real_call)

    mock_chunk_1 = MagicMock()
    mock_chunk_1.usage_metadata = None
    mock_chunk_1.function_calls = [real_call]
    mock_chunk_1.candidates = [MagicMock(content=MagicMock(parts=[real_part]))]
    mock_chunk_1.text = None

    mock_chunk_2 = MagicMock()
    mock_chunk_2.usage_metadata = MagicMock(prompt_token_count=15, candidates_token_count=18)
    mock_chunk_2.function_calls = None
    mock_chunk_2.candidates = None
    mock_chunk_2.text = "He creado el archivo exitosamente."

    calls_count = 0
    async def mock_generate_stream(*args, **kwargs):
        async def _gen():
            nonlocal calls_count
            calls_count += 1
            if calls_count == 1:
                yield mock_chunk_1
            else:
                yield mock_chunk_2
        return _gen()

    approval_mock = AsyncMock(return_value=False)
    mock_client = MagicMock()
    mock_client.aio.models.generate_content_stream = mock_generate_stream
    service._client = mock_client

    chunks = []
    async for chunk in service.stream_message(
        f"Crea {new_doc}",
        on_approval_request=approval_mock
    ):
        chunks.append(chunk)

    # Archivo nuevo no es destructivo -> No debe requerir HITL approval
    assert approval_mock.await_count == 0
    assert new_doc.exists()
    assert new_doc.read_text(encoding="utf-8") == "Contenido generado por Halibel"
    full_output = "".join(chunks)
    assert "Ejecutando: write_file" in full_output
    assert "He creado el archivo exitosamente." in full_output


def test_history_budget_trimming():
    """Prueba que el podado de historial respete el presupuesto máximo de caracteres."""
    from app.services.llm import _trim_history_to_budget

    history = [
        {"role": "user", "content": "Mensaje antiguo 1 " + ("A" * 500)},
        {"role": "model", "content": "Respuesta antigua 1 " + ("B" * 500)},
        {"role": "user", "content": "Mensaje intermedio " + ("C" * 2000)},
        {"role": "model", "content": "Respuesta reciente " + ("D" * 200)},
    ]

    # Con presupuesto reducido a 1000 caracteres, debe conservar solo los más recientes
    trimmed = _trim_history_to_budget(history, max_total_chars=1000, max_single_chars=500)
    assert len(trimmed) >= 1
    assert trimmed[-1]["role"] == "model"
    assert "Respuesta reciente" in trimmed[-1]["content"]

    # Mensaje individual excesivamente largo debe truncarse
    huge_history = [{"role": "user", "content": "X" * 10_000}]
    trimmed_huge = _trim_history_to_budget(huge_history, max_total_chars=16_000, max_single_chars=2_000)
    assert len(trimmed_huge) == 1
    assert len(trimmed_huge[0]["content"]) < 3_000
    assert "truncado" in trimmed_huge[0]["content"]


@pytest.mark.anyio
async def test_execute_tool_safely_timeout(monkeypatch):
    """Prueba que una herramienta que demore más del timeout configurado sea abortada limpiamente."""
    import time

    import app.services.llm as llm_mod
    from app.services.llm import _execute_tool_safely

    # Configuramos un timeout ultracorto para la prueba (0.1 segundos)
    monkeypatch.setattr(llm_mod, "TOOL_EXECUTION_TIMEOUT_SECONDS", 0.1)

    def slow_tool(tool_name, arguments):
        time.sleep(0.3)
        return {"status": "success"}

    monkeypatch.setattr("app.services.llm.execute_tool", slow_tool)

    result = await _execute_tool_safely("slow_tool", {})
    assert result["status"] == "error"
    assert "excedió el tiempo límite" in result["error"]





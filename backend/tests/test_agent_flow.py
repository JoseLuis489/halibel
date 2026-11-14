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



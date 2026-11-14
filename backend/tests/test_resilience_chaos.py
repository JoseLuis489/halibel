import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.services.llm import LLMService
from app.services.usage import UsageService
from app.tools.filesystem import list_directory, read_file, safe_delete_file
from app.tools.registry import execute_tool
from fastapi.testclient import TestClient
from google.genai.errors import APIError

client = TestClient(app)

# =====================================================================
# GRUPO 1: CAOS EN EL SISTEMA DE ARCHIVOS (FILESYSTEM RESILIENCE)
# =====================================================================

def test_read_file_exceeds_size_limit(tmp_path: Path):
    """
    Prueba de bomba de memoria: intenta leer un archivo que supera los 500 KB.
    - Qué es: Prueba de límite de tamaño preventivo (MAX_READ_FILE_SIZE_BYTES).
    - Para qué se usa: Garantizar que Halibel no cargue archivos gigantes en memoria ni sature el contexto.
    - Qué conecta: Valida la guarda de seguridad en `app.tools.filesystem.read_file`.
    """
    giant_file = tmp_path / "giant_archive.log"
    # Crear un archivo de 600 KB (límite es 500 KB)
    giant_file.write_bytes(b"A" * (600 * 1024))
    
    result = read_file(str(giant_file))
    assert result["status"] == "error"
    assert "supera el límite de seguridad" in result["error"]
    assert "500 KB" in result["error"]


def test_read_binary_corrupted_file(tmp_path: Path):
    """
    Prueba de archivo binario o corrupto (no UTF-8).
    - Qué es: Prueba de manejo de codificación de caracteres.
    - Para qué se usa: Evitar que archivos ejecutables o imágenes lancen un UnicodeDecodeError que tumbe el hilo.
    - Qué conecta: Valida la captura de excepciones en `app.tools.filesystem.read_file`.
    """
    binary_file = tmp_path / "sample_binary.bin"
    # Escribir bytes no válidos para UTF-8 (secuencias inválidas)
    binary_file.write_bytes(b"\x80\x81\xff\xfe\x00\xaa\xbb\xcc")
    
    result = read_file(str(binary_file))
    assert result["status"] == "error"
    assert "parece ser binario" in result["error"]


def test_safe_delete_race_condition(tmp_path: Path):
    """
    Prueba de condición de carrera: borrar un archivo que desaparece justo antes de la llamada.
    - Qué es: Prueba de concurrencia y tolerancia a fallos del sistema de archivos.
    - Para qué se usa: Garantizar que un archivo borrado previamente por otro proceso no cause un crash.
    - Qué conecta: Valida la verificación de existencia en `app.tools.filesystem.safe_delete_file`.
    """
    ghost_path = tmp_path / "ghost_file.txt"
    # Aseguramos que NO exista
    if ghost_path.exists():
        ghost_path.unlink()
        
    result = safe_delete_file(str(ghost_path))
    assert result["status"] == "error"
    assert "no existe para ser eliminado" in result["error"]


def test_filesystem_boundary_type_mismatches(tmp_path: Path):
    """
    Prueba de tipos cruzados: intentar leer carpetas como archivos o listar archivos como carpetas.
    - Qué es: Prueba de límites de tipos de nodos en el sistema de archivos.
    - Para qué se usa: Prevenir fallos si el modelo confunde el tipo de objeto en el disco.
    - Qué conecta: Valida las comprobaciones is_dir() e is_file() de `app.tools.filesystem`.
    """
    dummy_dir = tmp_path / "a_directory"
    dummy_dir.mkdir()
    
    dummy_file = tmp_path / "a_file.txt"
    dummy_file.write_text("archivo plano")
    
    # 1. Leer una carpeta con read_file
    res_dir_read = read_file(str(dummy_dir))
    assert res_dir_read["status"] == "error"
    assert "no es un archivo regular" in res_dir_read["error"]
    
    # 2. Listar un archivo con list_directory
    res_file_list = list_directory(str(dummy_file))
    assert res_file_list["status"] == "error"
    assert "no es un directorio" in res_file_list["error"]
    
    # 3. Intentar eliminar una carpeta con safe_delete_file (debe rechazarla, solo archivos)
    res_del_dir = safe_delete_file(str(dummy_dir))
    assert res_del_dir["status"] == "error"
    assert "solo elimina archivos individuales" in res_del_dir["error"]


def test_execute_tool_invalid_name_and_args():
    """
    Prueba de inyección de nombres de herramientas no registradas o argumentos inválidos.
    - Qué es: Prueba de seguridad del despachador de herramientas.
    - Para qué se usa: Asegurar que el modelo no pueda invocar funciones arbitrarias de Python.
    - Qué conecta: Valida el despachador `app.tools.registry.execute_tool`.
    """
    # Herramienta inexistente
    res_unknown = execute_tool("formatear_disco_duro", {})
    assert res_unknown["status"] == "error"
    assert "no disponible" in res_unknown["error"]
    
    # Argumentos no conformes (pasa argumentos que la función no acepta)
    res_bad_args = execute_tool("read_file", {"parametro_falso": 123})
    assert res_bad_args["status"] == "error"
    assert "Error ejecutando" in res_bad_args["error"]


# =====================================================================
# GRUPO 2: CAOS EN WEBSOCKETS (NETWORK & FLOOD RESILIENCE)
# =====================================================================

def test_websocket_flood_and_cancel_storm():
    """
    Prueba de tormenta de mensajes (Flood): envía ráfagas continuas de mensajes y cancelaciones.
    - Qué es: Prueba de estrés de concurrencia sobre el bucle del WebSocket.
    - Para qué se usa: Verificar que el servidor descarte tareas anteriores y no colapse la memoria.
    - Qué conecta: Endpoint `/api/v1/ws/chat`.
    """
    with client.websocket_connect("/api/v1/ws/chat") as ws:
        # Enviar 25 mensajes rápidamente intercalados con cancelaciones
        for i in range(25):
            ws.send_json({"type": "chat", "message": f"Mensaje rápido #{i}"})
            if i % 2 == 0:
                ws.send_json({"type": "cancel"})
                
        # Enviar cancelación final para detener cualquier stream residual
        ws.send_json({"type": "cancel"})
        
        # El socket debe permanecer operativo sin cerrarse por saturación;
        # enviar un último ping/cancel y cerrar limpiamente comprueba que la conexión no crasheó
        ws.send_json({"type": "cancel"})
        ws.close()


def test_websocket_corrupted_payloads():
    """
    Prueba de payloads corruptos y tipos de datos aberrantes.
    - Qué es: Prueba de tolerancia de Pydantic en tiempo real sobre WebSockets.
    - Para qué se usa: Evitar que payloads malformados desconecten a Halibel.
    - Qué conecta: Validación `WSIncomingMessage` en `chat.py`.
    """
    with client.websocket_connect("/api/v1/ws/chat") as ws:
        # Payload 1: tipos invertidos o no válidos para el esquema
        ws.send_json({"type": "tool_approval_response", "approved": "no_es_booleano", "tool_call_id": 99999})
        err1 = ws.receive_json()
        assert err1["type"] == "error"
        assert "inválido" in err1["error"]
        
        # Payload 2: texto no JSON
        ws.send_text("texto-plano-no-json")
        err2 = ws.receive_json()
        assert err2["type"] == "error"


# =====================================================================
# GRUPO 3: CAOS EN LA API DE GEMINI (ERROR SIMULATION)
# =====================================================================

@pytest.mark.anyio
async def test_llm_rate_limit_429_graceful_handling():
    """
    Simulación de error HTTP 429 (Cuota excedida de Google Gemini).
    - Qué es: Prueba de resiliencia frente a límites de tasa de la API.
    - Para qué se usa: Asegurar que el usuario reciba un mensaje amigable y no una traza de error en crudo.
    - Qué conecta: Bloque `except APIError` en `app.services.llm.stream_message`.
    """
    service = LLMService()
    
    mock_client = MagicMock()
    # En google-genai, APIError recibe (code: int, response_json: Any)
    err = APIError(429, {"error": {"message": "Rate limit exceeded"}})
    mock_client.aio.models.generate_content_stream.side_effect = err
    service._client = mock_client
    
    chunks = []
    async for chunk in service.stream_message("Hola"):
        chunks.append(chunk)
        
    full_text = "".join(chunks)
    assert "límite de peticiones por minuto" in full_text


@pytest.mark.anyio
async def test_llm_google_outage_503_graceful_handling():
    """
    Simulación de caída de servidores de Google (HTTP 503 Service Unavailable).
    - Qué es: Prueba de resiliencia frente a fallos de infraestructura en la nube.
    - Para qué se usa: Informar al usuario con calma que el servicio de IA está caído.
    - Qué conecta: Bloque `except APIError` en `app.services.llm.stream_message`.
    """
    service = LLMService()
    
    mock_client = MagicMock()
    err = APIError(503, {"error": {"message": "Service backend unavailable"}})
    mock_client.aio.models.generate_content_stream.side_effect = err
    service._client = mock_client
    
    chunks = []
    async for chunk in service.stream_message("Hola"):
        chunks.append(chunk)
        
    full_text = "".join(chunks)
    assert "no está disponible temporalmente" in full_text


# =====================================================================
# GRUPO 4: CAOS EN BASE DE DATOS SQLITE (CONCURRENCY & BUSY LOCKS)
# =====================================================================

def test_sqlite_locked_database_resilience(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Simulación de bloqueo de base de datos (`SQLITE_BUSY / database is locked`).
    - Qué es: Prueba de aislamiento de fallos en el servicio de métricas.
    - Para qué se usa: Garantizar que si SQLite está bloqueado o dañado, el usuario pueda seguir chateando.
    - Qué conecta: Método `app.services.usage.UsageService.log_usage`.
    """
    test_db = tmp_path / "chaos_usage.db"
    monkeypatch.setattr("app.services.usage.DB_PATH", test_db)
    chaos_usage_service = UsageService()
    
    # Simular un bloqueo forzado en _get_connection
    with patch("app.services.usage.logger.exception") as mock_logger:  # noqa: SIM117
        with patch.object(chaos_usage_service, "_get_connection", side_effect=sqlite3.OperationalError("database is locked")):
            chaos_usage_service.log_usage("gemini-3.5-flash-lite", 100, 50)
            # Debe registrar la excepción en el log sin levantar un error no controlado al llamador
            assert mock_logger.called


def test_sqlite_concurrent_multithread_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Prueba de estrés concurrente: 10 hilos escribiendo simultáneamente en SQLite.
    - Qué es: Prueba de concurrencia con el modo WAL (Write-Ahead Logging).
    - Para qué se usa: Comprobar la resiliencia del timeout y la ausencia de corrupción en disco.
    - Qué conecta: `UsageService.log_usage` en entornos concurrentes.
    """
    import threading
    
    test_db = tmp_path / "concurrent_usage.db"
    monkeypatch.setattr("app.services.usage.DB_PATH", test_db)
    service = UsageService()
    
    errors = []
    
    def worker(worker_id: int):
        try:
            for i in range(10):
                service.log_usage(f"model-{worker_id}", 10 + i, 20 + i)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            
    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(errors) == 0, f"Ocurrieron errores de concurrencia: {errors}"
    stats = service.get_stats()
    # 10 hilos * 10 escrituras = 100 registros
    assert stats["total_requests"] == 100
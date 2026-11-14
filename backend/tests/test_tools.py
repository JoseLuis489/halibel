from app.tools.filesystem import list_directory, read_file, safe_delete_file
from app.tools.registry import execute_tool, is_destructive_tool


def test_list_directory():
    result = list_directory(".")
    assert result["status"] == "success"
    assert result["total_items"] > 0
    names = [item["name"] for item in result["items"]]
    assert "app" in names or "tests" in names or "desktop" in names or "backend" in names or "run-dev.sh" in names

def test_read_file():
    # Leer un archivo que sabemos que existe
    result = read_file("backend/requirements.txt")
    if result["status"] == "error":
        # Probar ruta relativa alternativa
        result = read_file("requirements.txt")
    assert result["status"] == "success"
    assert "fastapi" in result["content"]

def test_read_file_nonexistent():
    result = read_file("archivo_inexistente_12345.xyz")
    assert result["status"] == "error"

def test_safe_delete_file(tmp_path):
    # Crear archivo temporal
    temp_file = tmp_path / "test_delete_halibel.txt"
    temp_file.write_text("contenido temporal para prueba de borrado")
    assert temp_file.exists()

    # Ejecutar safe_delete_file
    result = safe_delete_file(str(temp_file))
    assert result["status"] == "success"
    assert not temp_file.exists()

def test_registry_policy():
    assert not is_destructive_tool("list_directory")
    assert not is_destructive_tool("read_file")
    assert is_destructive_tool("safe_delete_file")

    exec_res = execute_tool("list_directory", {"path": "."})
    assert exec_res["status"] == "success"


def test_read_file_encodings_and_bom(tmp_path):
    """Prueba decodificación de archivos con BOM (Windows) y codificación ANSI/CP1252."""
    # 1. Archivo UTF-8 con BOM
    bom_file = tmp_path / "bom_file.txt"
    bom_file.write_bytes(b"\xef\xbb\xbf" + b"Hola desde Windows con BOM")
    res_bom = read_file(str(bom_file))
    assert res_bom["status"] == "success"
    assert "Hola desde Windows con BOM" in res_bom["content"]
    assert not res_bom["content"].startswith("\ufeff")

    # 2. Archivo CP1252 / Latin-1 con acentos y eñes
    latin_file = tmp_path / "latin1_file.txt"
    latin_file.write_bytes("año de fundación, canción y pingüino".encode("latin-1"))
    res_latin = read_file(str(latin_file))
    assert res_latin["status"] == "success"
    assert "año de fundación" in res_latin["content"]


def test_list_directory_truncation_metadata(tmp_path):
    """Prueba que carpetas con más de 100 elementos incluyan metadata de truncamiento."""
    # Crear 105 archivos en tmp_path
    for i in range(105):
        (tmp_path / f"test_file_{i:03d}.txt").touch()

    result = list_directory(str(tmp_path))
    assert result["status"] == "success"
    assert result["truncated"] is True
    assert result["total_items"] == 105
    assert result["shown_items"] == 100
    assert len(result["items"]) == 100


def test_safe_delete_file_locked_process(tmp_path, monkeypatch):
    """Prueba mensaje amigable cuando un archivo está bloqueado por otro proceso (WinError 32)."""
    locked_file = tmp_path / "locked_document.docx"
    locked_file.write_text("documento abierto")

    import send2trash

    def mock_send2trash_locked(path):
        err = PermissionError("The process cannot access the file because it is being used by another process.")
        err.winerror = 32
        raise err

    monkeypatch.setattr(send2trash, "send2trash", mock_send2trash_locked)

    result = safe_delete_file(str(locked_file))
    assert result["status"] == "error"
    assert "en uso por otro programa" in result["error"]


def test_security_policy_blocks_secrets(tmp_path):
    """Prueba que el perímetro de seguridad bloquee .env, llaves SSH y certificados."""
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=12345")
    res_env = read_file(str(env_file))
    assert res_env["status"] == "error"
    assert "política de seguridad" in res_env["error"]

    key_file = tmp_path / "id_rsa"
    key_file.write_text("-----BEGIN RSA PRIVATE KEY-----")
    res_key = read_file(str(key_file))
    assert res_key["status"] == "error"
    assert "política de seguridad" in res_key["error"]


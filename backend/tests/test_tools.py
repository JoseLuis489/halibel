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


def test_write_file_creation_and_overwrite(tmp_path):
    """Prueba creación segura de archivos y control de sobrescritura."""
    from app.tools.filesystem import write_file

    new_file = tmp_path / "subfolder" / "nota.txt"
    # 1. Crear nuevo archivo en subcarpeta inexistente (debe crear directorios automáticamente)
    res_create = write_file(str(new_file), "Hola Halibel", overwrite=False)
    assert res_create["status"] == "success"
    assert new_file.exists()
    assert new_file.read_text(encoding="utf-8") == "Hola Halibel"
    assert res_create["bytes_written"] == len(b"Hola Halibel")

    # 2. Intentar escribir sobre archivo existente sin flag overwrite -> Error controlado
    res_no_ow = write_file(str(new_file), "Contenido nuevo", overwrite=False)
    assert res_no_ow["status"] == "error"
    assert res_no_ow.get("already_exists") is True
    assert new_file.read_text(encoding="utf-8") == "Hola Halibel"

    # 3. Sobrescribir con overwrite=True -> Éxito
    res_ow = write_file(str(new_file), "Contenido actualizado", overwrite=True)
    assert res_ow["status"] == "success"
    assert new_file.read_text(encoding="utf-8") == "Contenido actualizado"


def test_write_file_security_blocks(tmp_path):
    """Prueba que write_file respete el perímetro anti-exfiltración de secretos."""
    from app.tools.filesystem import write_file

    # Bloqueo de archivo .env
    env_target = tmp_path / ".env"
    res_env = write_file(str(env_target), "API_KEY=hacked")
    assert res_env["status"] == "error"
    assert "política de seguridad" in res_env["error"]

    # Bloqueo dentro de carpeta .git
    git_target = tmp_path / ".git" / "evil.txt"
    res_git = write_file(str(git_target), "malicious")
    assert res_git["status"] == "error"
    assert "política de seguridad" in res_git["error"]

    # Bloqueo de extensión de clave privada
    key_target = tmp_path / "server.key"
    res_key = write_file(str(key_target), "cert")
    assert res_key["status"] == "error"
    assert "política de seguridad" in res_key["error"]


def test_tool_registry_and_approval_description(tmp_path):
    """Prueba que is_destructive_tool y get_tool_approval_description distingan sobrescritura."""
    from app.tools.registry import get_tool_approval_description, is_destructive_tool

    # safe_delete_file siempre es destructiva
    assert is_destructive_tool("safe_delete_file") is True
    del_desc = get_tool_approval_description("safe_delete_file", {"path": "test.txt"})
    assert "papelera de reciclaje" in del_desc

    # write_file para archivo nuevo NO es destructiva
    unborn_file = tmp_path / "nuevo_archivo.txt"
    assert is_destructive_tool("write_file", {"path": str(unborn_file), "overwrite": False}) is False

    # write_file para archivo existente SÍ es destructiva
    existing_file = tmp_path / "existente.txt"
    existing_file.write_text("ya estoy aquí")
    assert is_destructive_tool("write_file", {"path": str(existing_file), "overwrite": True}) is True

    ow_desc = get_tool_approval_description("write_file", {"path": str(existing_file)})
    assert "sobrescribir" in ow_desc

    # write_file para script ejecutable nuevo (.sh, .bat, etc.) SÍ requiere autorización interactiva
    unborn_script = tmp_path / "deploy.sh"
    assert is_destructive_tool("write_file", {"path": str(unborn_script), "overwrite": False}) is True
    script_desc = get_tool_approval_description("write_file", {"path": str(unborn_script)})
    assert "ejecutable" in script_desc or "script" in script_desc


def test_memory_tools_execution(tmp_path, monkeypatch):
    """Prueba las herramientas save_user_memory, recall_memories y forget_user_memory."""
    import app.tools.memory as mem_tools
    from app.services.memory import MemoryService

    # Aislar base de datos en tmp_path
    test_db = tmp_path / "test_memory_tools.db"
    isolated_svc = MemoryService(db_path=test_db)
    monkeypatch.setattr(mem_tools, "memory_service", isolated_svc)

    # 1. Guardar recuerdo
    save_res = mem_tools.save_user_memory(
        category="preference",
        content="El usuario prefiere Python sobre JavaScript",
        key="fav_language",
    )
    assert save_res["status"] == "success"
    assert save_res["memory"]["key"] == "fav_language"

    # 2. Consultar recuerdo
    recall_res = mem_tools.recall_memories(query="Python")
    assert recall_res["status"] == "success"
    assert recall_res["count"] == 1
    assert "Python" in recall_res["memories"][0]["content"]

    # 3. Olvidar recuerdo
    forget_res = mem_tools.forget_user_memory(key="fav_language")
    assert forget_res["status"] == "success"

    # Verificar que ya no existe
    recall_after = mem_tools.recall_memories(query="Python")
    assert recall_after["count"] == 0


def test_startup_and_system_files_protection(tmp_path):
    """Prueba que se bloquee el acceso y escritura en archivos de autoarranque y rutas del SO."""
    from pathlib import Path

    from app.tools.filesystem import _check_security_policy, write_file

    # 1. Bloqueo de archivos de inicio de shell (~/.bashrc, ~/.zshrc, .profile)
    for startup_file in [".bashrc", ".bash_profile", ".zshrc", ".profile"]:
        target = tmp_path / startup_file
        res = write_file(str(target), "# malware injection")
        assert res["status"] == "error"
        assert "archivo de inicio" in res["error"] or "política de seguridad" in res["error"]

    # 2. Bloqueo de rutas del sistema (/etc, /usr, Windows/SystemRoot)
    fake_etc = Path("/etc/cron.d/malicious_job")
    sec_err = _check_security_policy(fake_etc, is_write=True)
    assert sec_err is not None
    assert "protegido" in sec_err


def test_write_file_executable_extension_supervision(tmp_path):
    """Prueba que los archivos ejecutables (.sh, .bat, .ps1) exijan autorización explícita."""
    from app.tools.filesystem import write_file

    script_path = tmp_path / "script.sh"

    # Intento sin autorización explícita -> Debe ser bloqueado requiriendo aprobación
    res_blocked = write_file(str(script_path), "echo 'hello'", allow_executable=False)
    assert res_blocked["status"] == "error"
    assert res_blocked.get("requires_approval") is True
    assert "autorización humana explícita" in res_blocked["error"]

    # Intento con autorización explícita (simulando aprobación del usuario) -> Debe ejecutarse
    res_allowed = write_file(str(script_path), "echo 'hello'", allow_executable=True)
    assert res_allowed["status"] == "success"
    assert script_path.exists()
    assert script_path.read_text(encoding="utf-8") == "echo 'hello'"




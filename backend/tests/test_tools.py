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

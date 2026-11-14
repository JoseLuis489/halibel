import logging
from collections.abc import Callable
from typing import Any

from app.tools.filesystem import list_directory, read_file, safe_delete_file

logger = logging.getLogger(__name__)

# Herramientas expuestas directamente a la API de Google Gemini
AVAILABLE_TOOLS: list[Callable[..., Any]] = [
    list_directory,
    read_file,
    safe_delete_file
]

# Mapa de despacho por nombre de función
TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "list_directory": list_directory,
    "read_file": read_file,
    "safe_delete_file": safe_delete_file
}

# Políticas de seguridad (Human-in-the-Loop)
READ_ONLY_TOOLS = {"list_directory", "read_file"}
DESTRUCTIVE_TOOLS = {"safe_delete_file"}


def is_destructive_tool(tool_name: str) -> bool:
    """
    Determina si una herramienta requiere autorización explícita del usuario antes de ejecutarse.
    - Qué es: Función de política de seguridad.
    - Para qué se usa: Distinguir acciones seguras (lectura) de acciones destructivas (borrado).
    """
    return tool_name in DESTRUCTIVE_TOOLS


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Ejecuta de forma controlada una herramienta del catálogo por su nombre.
    - Qué es: Despachador de ejecución de herramientas.
    - Para qué se usa: Invocar la función de Python correspondiente desempaquetando sus argumentos.
    """
    func = TOOL_DISPATCH.get(tool_name)
    if not func:
        logger.error("Herramienta solicitada no encontrada en el registro: %s", tool_name)
        return {"status": "error", "error": f"Herramienta '{tool_name}' no disponible."}

    logger.info("Ejecutando herramienta '%s' con argumentos: %s", tool_name, arguments)
    try:
        return func(**arguments)
    except Exception as e:
        logger.exception("Excepción durante la ejecución de la herramienta '%s'", tool_name)
        return {"status": "error", "error": f"Error ejecutando '{tool_name}': {e!s}"}

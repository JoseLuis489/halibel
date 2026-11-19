import logging
from collections.abc import Callable
from typing import Any

from app.tools.filesystem import (
    is_sensitive_write_target,
    list_directory,
    read_file,
    safe_delete_file,
    write_file,
)
from app.tools.memory import forget_user_memory, recall_memories, save_user_memory
from app.tools.web import fetch_web_page, web_search

logger = logging.getLogger(__name__)

# Herramientas expuestas directamente a la API de Google Gemini (Function Calling)
AVAILABLE_TOOLS: list[Callable[..., Any]] = [
    list_directory,
    read_file,
    write_file,
    safe_delete_file,
    save_user_memory,
    recall_memories,
    forget_user_memory,
    web_search,
    fetch_web_page,
]

# Mapa de despacho por nombre de función para ejecución controlada
TOOL_DISPATCH: dict[str, Callable[..., Any]] = {
    "list_directory": list_directory,
    "read_file": read_file,
    "write_file": write_file,
    "safe_delete_file": safe_delete_file,
    "save_user_memory": save_user_memory,
    "recall_memories": recall_memories,
    "forget_user_memory": forget_user_memory,
    "web_search": web_search,
    "fetch_web_page": fetch_web_page,
}

# Políticas de clasificación base
READ_ONLY_TOOLS = {"list_directory", "read_file", "recall_memories", "web_search", "fetch_web_page"}
ALWAYS_DESTRUCTIVE_TOOLS = {"safe_delete_file"}


def is_destructive_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
    """
    Determina si una herramienta requiere autorización interactiva previa (Human-in-the-Loop).
    - Qué es: Evaluador determinista de impacto de herramientas.
    - Para qué se usa: Distinguir acciones inocuas de acciones destructivas o sensibles.
      `safe_delete_file` siempre requiere aprobación.
      `write_file` requiere aprobación si el archivo destino ya existe o si es un script/ejecutable.
    - Qué conecta: Consumido por `LLMService.stream_message` antes de invocar cualquier herramienta.
    """
    if tool_name in ALWAYS_DESTRUCTIVE_TOOLS:
        return True

    if tool_name == "write_file" and arguments:
        raw_path = arguments.get("path")
        overwrite = bool(arguments.get("overwrite", False))
        if raw_path:
            try:
                is_sens, _ = is_sensitive_write_target(str(raw_path), overwrite=overwrite)
                return is_sens
            except Exception:  # noqa: BLE001
                # Ante cualquier error o ambigüedad en la ruta, requerir autorización por seguridad
                return True

    return False


def get_tool_approval_description(tool_name: str, arguments: dict[str, Any]) -> str:
    """
    Construye una descripción legible y contextual para la tarjeta de aprobación en la UI.
    - Qué es: Generador de descripciones informativas de acciones bajo supervisión.
    - Para qué se usa: Explicar claramente al usuario en la interfaz gráfica qué va a ocurrir
      (ej: borrado de archivo, sobrescritura o creación de scripts ejecutables).
    - Qué conecta: Enviado en el payload WebSocket de aprobación hacia `desktop/src/App.tsx`.
    """
    target_path = str(arguments.get("path", "archivo"))
    if tool_name == "safe_delete_file":
        return f"Halibel solicita enviar el archivo '{target_path}' a la papelera de reciclaje."
    if tool_name == "write_file":
        overwrite = bool(arguments.get("overwrite", False))
        _, desc = is_sensitive_write_target(target_path, overwrite=overwrite)
        return desc or f"Halibel solicita autorización para escribir en '{target_path}'."
    return f"Halibel solicita autorización para ejecutar la herramienta '{tool_name}'."


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Ejecuta de forma controlada y tipada una herramienta del catálogo por su nombre.
    - Qué es: Despachador central de Function Calling.
    - Para qué se usa: Desempaquetar los argumentos proporcionados por Gemini e invocar la función de Python.
    - Qué conecta: Puente entre el orquestador del agente en `llm.py` y los módulos de herramientas en `app/tools/`.
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

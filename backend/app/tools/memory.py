import logging
from typing import Any

from app.services.memory import memory_service

logger = logging.getLogger(__name__)


def save_user_memory(category: str, content: str, key: str = "") -> dict[str, Any]:
    """
    Almacena o actualiza un recuerdo o preferencia sobre el usuario en la memoria a largo plazo.
    - Qué es: Herramienta de persistencia cognitiva para retener información entre sesiones.
    - Para qué se usa: Guardar preferencias (ej: 'idioma español', 'modo oscuro'), datos sobre proyectos
      (ej: 'trabajando en la app Halibel'), datos biográficos o directivas especiales.
      Si se proporciona una clave ('key') como 'user_name', actualizará el registro existente sin duplicarlo.
    - Qué conecta: Llama a `memory_service.save_memory` en SQLite local.
    """
    try:
        if not content or not content.strip():
            return {"status": "error", "error": "El contenido del recuerdo no puede estar vacío."}

        clean_category = category.strip().lower() if category else "preference"
        clean_key = key.strip() if key else None

        saved = memory_service.save_memory(
            category=clean_category,
            content=content.strip(),
            key=clean_key,
        )
        logger.info("Recuerdo guardado con éxito: [%s] key=%s id=%s", clean_category, clean_key, saved.get("id"))
        return {
            "status": "success",
            "message": f"Recuerdo registrado correctamente bajo la categoría '{clean_category}'.",
            "memory": saved,
        }
    except Exception as e:
        logger.exception("Error al guardar recuerdo en save_user_memory")
        return {"status": "error", "error": f"Error al guardar en memoria: {e!s}"}


def recall_memories(query: str = "", category: str = "") -> dict[str, Any]:
    """
    Busca y recupera recuerdos del usuario previamente almacenados en la memoria a largo plazo.
    - Qué es: Herramienta de consulta y rememoración semántica de datos del usuario.
    - Para qué se usa: Consultar gustos, decisiones previas, notas de proyectos o datos del usuario
      cuando se necesite contexto adicional antes de responder o ejecutar una tarea.
    - Qué conecta: Llama a `memory_service.search_memories` en SQLite local.
    """
    try:
        clean_query = query.strip() if query else None
        clean_category = category.strip().lower() if category else None

        memories = memory_service.search_memories(
            query=clean_query,
            category=clean_category,
            limit=15,
        )
        return {
            "status": "success",
            "count": len(memories),
            "memories": memories,
        }
    except Exception as e:
        logger.exception("Error al recuperar recuerdos en recall_memories")
        return {"status": "error", "error": f"Error al buscar en memoria: {e!s}"}


def forget_user_memory(memory_id: int = 0, key: str = "") -> dict[str, Any]:
    """
    Elimina un recuerdo específico de la memoria a largo plazo a petición del usuario.
    - Qué es: Herramienta de olvido o descarte de información obsoleta o errónea.
    - Para qué se usa: Permitir al usuario pedir a Halibel que olvide una preferencia o hecho específico.
    - Qué conecta: Llama a `memory_service.delete_memory` o `delete_memory_by_key`.
    """
    try:
        clean_key = key.strip() if key else None
        deleted = False

        if clean_key:
            deleted = memory_service.delete_memory_by_key(clean_key)
            target = f"con clave '{clean_key}'"
        elif memory_id > 0:
            deleted = memory_service.delete_memory(memory_id)
            target = f"con ID {memory_id}"
        else:
            return {
                "status": "error",
                "error": "Debes especificar al menos un 'memory_id' numérico o una 'key' para olvidar el recuerdo.",
            }

        if deleted:
            return {"status": "success", "message": f"Recuerdo {target} eliminado con éxito de la memoria."}
        return {"status": "not_found", "message": f"No se encontró ningún recuerdo {target} para eliminar."}
    except Exception as e:
        logger.exception("Error al eliminar recuerdo en forget_user_memory")
        return {"status": "error", "error": f"Error al borrar recuerdo: {e!s}"}

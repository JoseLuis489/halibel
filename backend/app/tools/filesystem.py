from pathlib import Path
from typing import Any

import send2trash

# Límite máximo de tamaño de archivo para lectura (500 KB) para proteger el contexto y la memoria
MAX_READ_FILE_SIZE_BYTES = 500 * 1024

def list_directory(path: str = ".") -> dict[str, Any]:
    """
    Lista los archivos y subdirectorios de una ruta de forma segura y estructurada.
    - Qué es: Herramienta de solo lectura (Read-Only) del sistema de archivos.
    - Para qué se usa: Permitir a Halibel inspeccionar carpetas a petición del usuario.
    - Qué conecta: Lee el sistema operativo local usando `pathlib.Path` y devuelve metadatos a Gemini.
    """
    try:
        target_path = Path(path).expanduser().resolve()
        if not target_path.exists():
            return {"status": "error", "error": f"La ruta '{path}' no existe."}
        if not target_path.is_dir():
            return {"status": "error", "error": f"La ruta '{path}' no es un directorio."}

        items = []
        # Listar contenido limitando a un máximo de 100 elementos para no saturar el prompt
        for entry in sorted(target_path.iterdir())[:100]:
            is_dir = entry.is_dir()
            size_kb = None
            if not is_dir:
                try:
                    size_kb = round(entry.stat().st_size / 1024, 2)
                except Exception:  # noqa: BLE001
                    size_kb = None

            items.append({
                "name": entry.name,
                "type": "directory" if is_dir else "file",
                "size_kb": size_kb
            })

        return {
            "status": "success",
            "path": str(target_path),
            "total_items": len(items),
            "items": items
        }

    except PermissionError:
        return {"status": "error", "error": f"Permiso denegado para acceder a '{path}'."}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Error al listar directorio: {e!s}"}


def read_file(path: str) -> dict[str, Any]:
    """
    Lee el contenido textual de un archivo con límite preventivo de tamaño.
    - Qué es: Herramienta de solo lectura (Read-Only) para inspección de documentos de texto/código.
    - Para qué se usa: Permitir a Halibel resumir, explicar o analizar el contenido de un archivo local.
    - Qué conecta: Lee el archivo del disco local y entrega el texto directamente al contexto de Gemini.
    """
    try:
        file_path = Path(path).expanduser().resolve()
        if not file_path.exists():
            return {"status": "error", "error": f"El archivo '{path}' no existe."}
        if not file_path.is_file():
            return {"status": "error", "error": f"La ruta '{path}' no es un archivo regular."}

        # Verificación de tamaño preventivo
        file_size = file_path.stat().st_size
        if file_size > MAX_READ_FILE_SIZE_BYTES:
            return {
                "status": "error",
                "error": f"El archivo supera el límite de seguridad de lectura ({file_size / 1024:.1f} KB > 500 KB)."
            }

        # Intentar lectura UTF-8
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "status": "error",
                "error": f"El archivo '{file_path.name}' parece ser binario y no texto plano legible."
            }

        return {
            "status": "success",
            "path": str(file_path),
            "filename": file_path.name,
            "size_kb": round(file_size / 1024, 2),
            "content": content
        }

    except PermissionError:
        return {"status": "error", "error": f"Permiso denegado para leer '{path}'."}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Error al leer archivo: {e!s}"}


def safe_delete_file(path: str) -> dict[str, Any]:
    """
    Envía un archivo a la Papelera de Reciclaje del sistema operativo (Soft Delete).
    - Qué es: Herramienta de acción sensible / destructiva supervisada.
    - Para qué se usa: Permitir a Halibel eliminar archivos de forma 100% reversible usando send2trash.
    - Qué conecta: Interactúa con la Papelera de Reciclaje de Windows/Linux tras la autorización del usuario.
    """
    try:
        target_path = Path(path).expanduser().resolve()
        if not target_path.exists():
            return {"status": "error", "error": f"El archivo '{path}' no existe para ser eliminado."}
        if not target_path.is_file():
            return {"status": "error", "error": f"'{path}' no es un archivo (Halibel solo elimina archivos individuales)."}

        # Envío seguro a la papelera del SO
        send2trash.send2trash(str(target_path))

        return {
            "status": "success",
            "message": f"El archivo '{target_path.name}' fue enviado a la papelera de reciclaje con éxito.",
            "path": str(target_path)
        }

    except PermissionError:
        return {"status": "error", "error": f"Permiso denegado para enviar a la papelera '{path}'."}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"Error al enviar a la papelera: {e!s}"}
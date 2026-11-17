import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

import send2trash

logger = logging.getLogger(__name__)

# Límite máximo de tamaño de archivo para lectura (500 KB) para proteger el contexto y la memoria
MAX_READ_FILE_SIZE_BYTES = 500 * 1024

# Límite máximo de tamaño de archivo para escritura (2 MB) para prevenir escrituras excesivas
MAX_WRITE_FILE_SIZE_BYTES = 2 * 1024 * 1024

# =====================================================================
# POLÍTICA DE SEGURIDAD: PERÍMETRO ANTI-EXFILTRACIÓN Y ANTI-PERSISTENCIA
# =====================================================================

# Carpetas cuyo acceso (lectura, listado, escritura o borrado) está prohibido en cualquier nivel
BLOCKED_DIR_NAMES = frozenset({
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".gcloud",
    ".gnupg",
    ".kube",
    ".docker",
})

# Patrones precisos de archivos de credenciales y configuración sensible (case-insensitive)
BLOCKED_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    "known_hosts",
    "authorized_keys",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "credentials.toml",
    "*_credentials.*",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.toml",
    "secrets.env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "halibel.db*",  # Protege la propia base de datos SQLite de métricas/memoria de Halibel
)

# Extensiones de certificados, llaves criptográficas y gestores de contraseñas
BLOCKED_EXTENSIONS = frozenset({
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".kdbx",
})

# Archivos de configuración de shell y autoarranque de sesión (anti-persistencia por Prompt Injection)
BLOCKED_STARTUP_FILENAMES = frozenset({
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".kshrc",
    ".cshrc",
    ".tcshrc",
    "config.fish",
    "authorized_keys",
    "crontab",
})

# Extensiones ejecutables o de scripting que exigen supervisión humana obligatoria (Human-in-the-Loop)
SENSITIVE_EXECUTABLE_EXTENSIONS = frozenset({
    ".sh",
    ".bash",
    ".zsh",
    ".bat",
    ".cmd",
    ".ps1",
    ".psm1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".wsf",
    ".wsh",
    ".exe",
    ".msi",
    ".scr",
    ".com",
    ".reg",
})


def _is_system_or_startup_path(resolved_path: Path) -> str | None:
    """
    Detecta si la ruta apunta a directorios críticos del sistema operativo o carpetas de autoarranque
    (Windows Startup, systemd, cron, /etc, /usr, C:\\Windows, etc.).
    """
    normalized_posix = resolved_path.as_posix().lower()
    name_lower = resolved_path.name.lower()

    # 1. Archivos de inicialización de shell (~/.bashrc, ~/.zshrc, ~/.profile, etc.)
    if name_lower in BLOCKED_STARTUP_FILENAMES:
        return f"Acceso denegado: '{resolved_path.name}' es un archivo de inicio del sistema/shell protegido."

    # 2. Carpeta Startup de Windows (AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup)
    if "start menu/programs/startup" in normalized_posix or "microsoft/windows/start menu" in normalized_posix:
        return "Acceso denegado: la carpeta de autoarranque (Startup) de Windows está protegida."

    # 3. Rutas de autoarranque en Linux/macOS (autostart, LaunchAgents, LaunchDaemons, systemd, cron)
    startup_subpaths = (
        "/.config/autostart",
        "/library/launchagents",
        "/library/launchdaemons",
        "/etc/systemd",
        "/etc/init.d",
        "/etc/cron",
    )
    if any(sub in normalized_posix for sub in startup_subpaths):
        return "Acceso denegado: los directorios de servicios y autoarranque del sistema están protegidos."

    # 4. Directorios raíz críticos de Windows
    if os.name == "nt":
        win_dir = Path(os.environ.get("SystemRoot", "C:\\Windows")).resolve()
        prog_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files")).resolve()
        prog_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")).resolve()
        for protected_root in (win_dir, prog_files, prog_files_x86):
            if resolved_path == protected_root or protected_root in resolved_path.parents:
                return f"Acceso denegado: el directorio del sistema '{protected_root}' está protegido."

    # 5. Directorios raíz críticos de Unix/Linux/macOS
    unix_protected_roots = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys", "/system")
    for root_prefix in unix_protected_roots:
        if normalized_posix == root_prefix or normalized_posix.startswith(f"{root_prefix}/"):
            return f"Acceso denegado: el directorio del sistema '{root_prefix}' está protegido."

    return None


def _check_security_policy(target_path: Path, is_write: bool = False) -> str | None:
    """
    Evalúa si una ruta viola la política de protección de secretos o integridad del sistema.
    - Qué es: Barrera de seguridad determinista previa a cualquier operación de I/O.
    - Para qué se usa: Bloquear el acceso a archivos de credenciales, llaves SSH, bases de datos internas,
      rutas de autoarranque y directorios críticos del SO.
    - Retorna: Un mensaje de error si el acceso está bloqueado, o `None` si la ruta es segura.
    """
    # 1. Verificar si algún directorio padre o el propio destino pertenece a carpetas críticas
    for part in target_path.parts:
        if part.lower() in BLOCKED_DIR_NAMES:
            logger.warning("Bloqueo de seguridad activado (directorio restringido): %s", target_path)
            return f"Acceso denegado por política de seguridad: el directorio '{part}' está protegido."

    name_lower = target_path.name.lower()

    # 2. Verificar extensiones criptográficas o sensibles
    if target_path.suffix.lower() in BLOCKED_EXTENSIONS:
        logger.warning("Bloqueo de seguridad activado (extensión restringida): %s", target_path)
        return f"Acceso denegado por política de seguridad: archivos '{target_path.suffix}' están protegidos."

    # 3. Verificar patrones de nombres de archivo sensibles (.env, id_rsa, credentials.json, etc.)
    for pattern in BLOCKED_FILE_PATTERNS:
        if fnmatch.fnmatch(name_lower, pattern):
            logger.warning("Bloqueo de seguridad activado (patrón '%s'): %s", pattern, target_path)
            return f"Acceso denegado por política de seguridad: '{target_path.name}' contiene información sensible."

    # 4. Verificar rutas del sistema operativo y archivos de autoarranque (especialmente crítico en escritura/borrado)
    system_error = _is_system_or_startup_path(target_path)
    if system_error and (is_write or target_path.name.lower() in BLOCKED_STARTUP_FILENAMES):
        logger.warning("Bloqueo de seguridad activado (ruta de sistema/autoarranque): %s", target_path)
        return system_error

    return None


def is_sensitive_write_target(path: str, overwrite: bool = False) -> tuple[bool, str]:
    """
    Helper público para `registry.py` y `llm.py`: determina si una operación `write_file`
    debe exigir aprobación humana (Human-in-the-Loop) antes de ejecutarse.
    - Exige confirmación si:
        1. El archivo destino es un script o ejecutable (.sh, .bat, .cmd, .ps1, .vbs, .exe, etc.).
        2. Se va a sobrescribir (`overwrite=True`) un archivo existente.
    """
    resolved_path = Path(path).expanduser().resolve()
    ext = resolved_path.suffix.lower()

    if ext in SENSITIVE_EXECUTABLE_EXTENSIONS:
        return (
            True,
            f"Halibel solicita crear o modificar un archivo ejecutable/script ('{resolved_path.name}').",
        )

    if resolved_path.exists() and resolved_path.is_file():
        return (
            True,
            f"Halibel solicita sobrescribir el archivo existente '{resolved_path.name}'.",
        )

    return False, ""


# =====================================================================
# HERRAMIENTAS DEL SISTEMA DE ARCHIVOS
# =====================================================================


def list_directory(path: str = ".") -> dict[str, Any]:
    """
    Lista los archivos y subdirectorios de una ruta de forma segura y estructurada,
    con filtrado de secretos, soporte de enlaces simbólicos y metadatos de truncamiento.
    - Qué es: Herramienta de solo lectura (Read-Only) del sistema de archivos.
    - Para qué se usa: Permitir a Halibel inspeccionar carpetas a petición del usuario sin desbordar el contexto.
    - Qué conecta: Lee el sistema operativo local usando `pathlib.Path` y devuelve metadatos a Gemini.
    """
    try:
        target_path = Path(path).expanduser().resolve()

        # Validar política de seguridad antes de tocar el sistema de archivos
        security_error = _check_security_policy(target_path)
        if security_error:
            return {"status": "error", "error": security_error}

        if not target_path.exists():
            return {"status": "error", "error": f"La ruta '{path}' no existe."}
        if not target_path.is_dir():
            return {"status": "error", "error": f"La ruta '{path}' no es un directorio."}

        visible_entries = []
        for entry in sorted(target_path.iterdir()):
            # Evaluamos tanto el nombre del enlace como su destino real (si existe) para evitar bypasses
            is_protected = (
                _check_security_policy(entry) is not None
                or _check_security_policy(entry.resolve()) is not None
            )
            if is_protected:
                continue
            visible_entries.append(entry)

        total_items = len(visible_entries)
        truncated = total_items > 100
        shown_entries = visible_entries[:100]

        items = []
        for entry in shown_entries:
            is_symlink = entry.is_symlink()
            is_dir = not is_symlink and entry.is_dir()
            size_kb = None

            if not is_dir and not is_symlink:
                try:
                    size_kb = round(entry.stat().st_size / 1024, 2)
                except OSError:
                    size_kb = None

            entry_type = "symlink" if is_symlink else ("directory" if is_dir else "file")
            items.append({
                "name": entry.name,
                "type": entry_type,
                "size_kb": size_kb,
            })

        return {
            "status": "success",
            "path": str(target_path),
            "total_items": total_items,
            "shown_items": len(items),
            "truncated": truncated,
            "items": items,
        }

    except PermissionError:
        return {"status": "error", "error": f"Permiso denegado para acceder a '{path}'."}
    except Exception as e:
        logger.exception("Error inesperado en list_directory para '%s'", path)
        return {"status": "error", "error": f"Error al listar directorio: {e!s}"}


def read_file(path: str) -> dict[str, Any]:
    """
    Lee el contenido textual de un archivo con límite preventivo de tamaño, filtro de secretos
    y soporte multimodelo de codificación (UTF-8 con/sin BOM y CP1252/Latin-1 para Windows).
    - Qué es: Herramienta de solo lectura (Read-Only) para inspección de documentos de texto/código.
    - Para qué se usa: Permitir a Halibel resumir, explicar o analizar el contenido de un archivo local.
    - Qué conecta: Lee el archivo del disco local y entrega el texto directamente al contexto de Gemini.
    """
    try:
        # resolve() sigue enlaces simbólicos, impidiendo bypasses tipo symlink -> .env
        file_path = Path(path).expanduser().resolve()

        # Barrera anti-exfiltración de secretos
        security_error = _check_security_policy(file_path)
        if security_error:
            return {"status": "error", "error": security_error}

        if not file_path.exists():
            return {"status": "error", "error": f"El archivo '{path}' no existe."}
        if not file_path.is_file():
            return {"status": "error", "error": f"La ruta '{path}' no es un archivo regular."}

        # Verificación de tamaño preventivo
        file_size = file_path.stat().st_size
        if file_size > MAX_READ_FILE_SIZE_BYTES:
            return {
                "status": "error",
                "error": f"El archivo supera el límite de seguridad de lectura ({file_size / 1024:.1f} KB > 500 KB).",
            }

        raw_bytes = file_path.read_bytes()

        # Heurística estándar de binarios (detección de byte nulo \x00 en los primeros 1024 bytes)
        if b"\x00" in raw_bytes[:1024]:
            return {
                "status": "error",
                "error": f"El archivo '{file_path.name}' parece ser binario y no texto plano legible.",
            }

        # Intentar decodificación en UTF-8 con soporte de BOM y fallback a CP1252 / Latin-1 para Windows
        content = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                content = raw_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            return {
                "status": "error",
                "error": f"No se pudo decodificar el texto del archivo '{file_path.name}' en UTF-8 ni CP1252.",
            }

        return {
            "status": "success",
            "path": str(file_path),
            "filename": file_path.name,
            "size_kb": round(file_size / 1024, 2),
            "content": content,
        }

    except PermissionError:
        return {"status": "error", "error": f"Permiso denegado para leer '{path}'."}
    except Exception as e:
        logger.exception("Error inesperado en read_file para '%s'", path)
        return {"status": "error", "error": f"Error al leer archivo: {e!s}"}


def safe_delete_file(path: str) -> dict[str, Any]:
    """
    Envía un archivo o enlace simbólico a la Papelera de Reciclaje del sistema operativo (Soft Delete).
    - Qué es: Herramienta de acción sensible / destructiva supervisada.
    - Para qué se usa: Permitir a Halibel eliminar archivos de forma 100% reversible usando send2trash.
    - Qué conecta: Interactúa con la Papelera de Reciclaje de Windows/Linux tras la autorización del usuario.
    """
    try:
        raw_path = Path(path).expanduser()
        resolved_path = raw_path.resolve()

        # Verificamos tanto la ruta solicitada como su destino real contra la política de seguridad
        security_error = (
            _check_security_policy(resolved_path, is_write=True)
            or _check_security_policy(raw_path, is_write=True)
        )
        if security_error:
            return {"status": "error", "error": security_error}

        # Determinamos la ruta física a enviar a la papelera:
        # Si es un symlink (incluso roto), enviamos el enlace en sí sin destruir el archivo destino original
        is_symlink = raw_path.is_symlink()
        target_to_trash = raw_path.parent.resolve() / raw_path.name if is_symlink else resolved_path

        if not is_symlink and not target_to_trash.exists():
            return {"status": "error", "error": f"El archivo '{path}' no existe para ser eliminado."}

        if not is_symlink and not target_to_trash.is_file():
            return {
                "status": "error",
                "error": f"'{path}' no es un archivo (Halibel solo elimina archivos individuales).",
            }

        # Envío seguro a la papelera del SO
        send2trash.send2trash(str(target_to_trash))

        return {
            "status": "success",
            "message": f"El archivo '{target_to_trash.name}' fue enviado a la papelera de reciclaje con éxito.",
            "path": str(target_to_trash),
        }

    except OSError as e:
        # En Windows, WinError 32 (ERROR_SHARING_VIOLATION) hereda de PermissionError/OSError
        winerror = getattr(e, "winerror", None)
        if winerror == 32 or "used by another process" in str(e).lower():
            return {
                "status": "error",
                "error": f"No se pudo eliminar '{path}': el archivo está abierto o en uso por otro programa en Windows.",
            }
        if isinstance(e, PermissionError):
            return {"status": "error", "error": f"Permiso denegado para enviar a la papelera '{path}'."}

        logger.exception("Error de sistema operativo en safe_delete_file para '%s'", path)
        return {"status": "error", "error": f"Error de sistema al enviar a la papelera: {e!s}"}

    except Exception as e:
        logger.exception("Error inesperado en safe_delete_file para '%s'", path)
        return {"status": "error", "error": f"Error al enviar a la papelera: {e!s}"}


def write_file(
    path: str,
    content: str,
    overwrite: bool = False,
    allow_executable: bool = False,
) -> dict[str, Any]:
    """
    Crea un nuevo archivo de texto o actualiza uno existente de manera controlada y segura.
    - Qué es: Herramienta de creación y edición controlada del sistema de archivos.
    - Para qué se usa: Permitir a Halibel escribir scripts, notas, documentos o configuraciones locales.
      Aplica el perímetro anti-exfiltración de secretos, bloquea rutas del sistema/autoarranque
      y exige autorización explícita para sobrescritura o scripts ejecutables.
    - Qué conecta: Crea o modifica archivos en el almacenamiento local del sistema operativo.
    """
    try:
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_WRITE_FILE_SIZE_BYTES:
            return {
                "status": "error",
                "error": (
                    f"El contenido ({len(content_bytes)} bytes) excede el tamaño máximo permitido de "
                    f"{MAX_WRITE_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                ),
            }

        raw_path = Path(path).expanduser()
        resolved_path = raw_path.resolve()

        # 1. Evaluación estricta de política de seguridad (secretos + autoarranque + sistema)
        security_error = (
            _check_security_policy(resolved_path, is_write=True)
            or _check_security_policy(raw_path, is_write=True)
        )
        if security_error:
            return {"status": "error", "error": security_error}

        # 2. Barrera para extensiones ejecutables o scripts (.sh, .bat, .cmd, .ps1, .vbs, .exe, etc.)
        if resolved_path.suffix.lower() in SENSITIVE_EXECUTABLE_EXTENSIONS and not allow_executable:
            logger.warning("Escritura de ejecutable bloqueada sin autorización previa: %s", resolved_path)
            return {
                "status": "error",
                "error": (
                    f"Por seguridad, escribir archivos ejecutables o scripts ('{resolved_path.suffix}') "
                    "requiere autorización humana explícita (allow_executable=True)."
                ),
                "requires_approval": True,
            }

        # 3. Protección contra sobrescritura accidental
        if resolved_path.exists():
            if resolved_path.is_dir():
                return {
                    "status": "error",
                    "error": f"La ruta '{path}' es un directorio existente. No se puede sobrescribir con un archivo.",
                }
            if not overwrite:
                return {
                    "status": "error",
                    "error": (
                        f"El archivo '{resolved_path.name}' ya existe en esa ubicación. "
                        "Para sobrescribirlo, debes confirmar explícitamente indicando overwrite=True."
                    ),
                    "already_exists": True,
                }

        # Asegurar creación de subdirectorios intermedios
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        resolved_path.write_text(content, encoding="utf-8")
        logger.info("Archivo escrito con éxito: %s (%d bytes)", resolved_path, len(content_bytes))

        return {
            "status": "success",
            "message": f"Archivo '{resolved_path.name}' guardado correctamente.",
            "path": str(resolved_path),
            "bytes_written": len(content_bytes),
        }

    except PermissionError:
        logger.warning("Permiso denegado al escribir en '%s'", path)
        return {"status": "error", "error": f"Permiso denegado por el sistema para escribir en '{path}'."}
    except OSError as e:
        logger.exception("Error de sistema operativo en write_file para '%s'", path)
        return {"status": "error", "error": f"Error de sistema operativo al escribir el archivo: {e!s}"}
    except Exception as e:
        logger.exception("Error inesperado en write_file para '%s'", path)
        return {"status": "error", "error": f"Error inesperado al escribir el archivo: {e!s}"}
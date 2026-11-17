import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Ubicación por defecto de la base de datos SQLite compartida:
# - En desarrollo: backend/data/halibel.db
# - En producción empaquetada (PyInstaller): %APPDATA%/Halibel/halibel.db (USER_DATA_DIR)
_DEV_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB_PATH = (
    settings.USER_DATA_DIR / "halibel.db"
    if getattr(sys, "frozen", False)
    else _DEV_DATA_DIR / "halibel.db"
)

# Límites preventivos para proteger el System Prompt y el rendimiento de consultas
MAX_MEMORY_CONTENT_CHARS = 600
MAX_MEMORY_KEY_CHARS = 80
MAX_MEMORY_CATEGORY_CHARS = 40
MAX_PROFILE_CONTEXT_CHARS = 4_000


class MemoryService:
    """
    Servicio de memoria persistente a largo plazo y almacenamiento de conversaciones.
    - Qué es: Gestor de persistencia en SQLite local estructurado para recuerdos episódicos/semánticos
      del usuario y el historial de sesiones de chat.
    - Para qué se usa: Permitir a Halibel recordar preferencias, datos biográficos, contexto de proyectos,
      y recuperar conversaciones previas con paginación por cursor sin depender de nubes externas.
    - Qué conecta: Provee datos al LLMService para enriquecer el System Prompt (`_build_runtime_context`),
      a las herramientas de agente (`app.tools.memory`) y a los endpoints WebSocket y REST de la interfaz.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """
        Inicializa el servicio de memoria asegurando la estructura de tablas y el modo WAL.
        - Qué es: Constructor del servicio con inyección opcional de ruta de base de datos.
        - Para qué se usa: Facilitar la inicialización normal o con rutas aisladas en pruebas unitarias.
        - Qué conecta: Asigna la ruta de base de datos y ejecuta `_ensure_db()`.
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_db()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Fábrica centralizada de conexiones SQLite con optimizaciones de concurrencia y bloqueo.
        - Qué es: Generador de conexiones locales con timeout y pragma de sincronización normal.
        - Para qué se usa: Evitar colisiones `database is locked` ante lecturas y escrituras simultáneas.
        - Qué conecta: Establece conexión directa con `self.db_path`.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _ensure_db(self) -> None:
        """
        Garantiza la existencia del directorio y de las tablas de recuerdos y sesiones en SQLite.
        - Qué es: Función DDL de inicialización de esquema relacional.
        - Para qué se usa: Crear las tablas `user_memories`, `chat_sessions` y `chat_messages`
          con sus índices de rendimiento y activar el modo WAL.
        - Qué conecta: Interactúa directamente con el archivo SQLite en disco.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("PRAGMA journal_mode = WAL;")

                # Tabla de recuerdos y perfil del usuario a largo plazo
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL,
                        key TEXT UNIQUE,
                        content TEXT NOT NULL,
                        importance INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memories_category 
                    ON user_memories(category);
                """)

                # Tabla de sesiones de chat
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)

                # Tabla de mensajes históricos por sesión (índice compuesto óptimo para paginación before_id)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata TEXT,
                        timestamp TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_session 
                    ON chat_messages(session_id, id);
                """)

            logger.info("Base de datos de memoria inicializada exitosamente en %s", self.db_path)
        except Exception:
            logger.exception("Error crítico al inicializar las tablas de memoria en %s", self.db_path)
            raise

    # -------------------------------------------------------------------------
    # Métodos de Gestión de Recuerdos del Usuario (Long-Term Memory)
    # -------------------------------------------------------------------------

    def save_memory(
        self,
        category: str,
        content: str,
        key: str | None = None,
        importance: int = 1,
    ) -> dict[str, Any]:
        """
        Almacena o actualiza un recuerdo o preferencia del usuario aplicando límites estrictos
        de longitud para evitar contaminar el System Prompt futuro.
        - Qué es: Operación de inserción o actualización (Upsert) de memoria semántica.
        - Para qué se usa: Registrar datos estables (nombre, profesión, proyectos, estilo de respuesta).
        """
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("El contenido del recuerdo no puede estar vacío.")

        # Truncamiento preventivo para evitar volcados masivos que disparen tokens en cada turno
        if len(clean_content) > MAX_MEMORY_CONTENT_CHARS:
            logger.warning(
                "Recuerdo truncado de %d a %d caracteres (category=%s, key=%s)",
                len(clean_content),
                MAX_MEMORY_CONTENT_CHARS,
                category,
                key,
            )
            clean_content = clean_content[: MAX_MEMORY_CONTENT_CHARS - 3].rstrip() + "..."

        clean_category = (category.strip().lower() or "general")[:MAX_MEMORY_CATEGORY_CHARS]
        clean_key = (
            key.strip().lower()[:MAX_MEMORY_KEY_CHARS]
            if key and key.strip()
            else None
        )
        # Normalizar nivel de importancia entre 1 y 5
        safe_importance = max(1, min(5, int(importance)))

        now = datetime.now().isoformat()

        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            if clean_key:
                cursor.execute(
                    """
                    INSERT INTO user_memories (category, key, content, importance, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        category = excluded.category,
                        content = excluded.content,
                        importance = excluded.importance,
                        updated_at = excluded.updated_at
                    RETURNING id, category, key, content, importance, created_at, updated_at;
                    """,
                    (clean_category, clean_key, clean_content, safe_importance, now, now),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO user_memories (category, key, content, importance, created_at, updated_at)
                    VALUES (?, NULL, ?, ?, ?, ?)
                    RETURNING id, category, key, content, importance, created_at, updated_at;
                    """,
                    (clean_category, clean_content, safe_importance, now, now),
                )

            row = cursor.fetchone()
            return dict(row)

    def get_memory_by_key(self, key: str) -> dict[str, Any] | None:
        """
        Recupera un recuerdo único por su clave identificadora.
        - Qué es: Consulta directa por índice único.
        - Para qué se usa: Obtener rápidamente configuraciones o datos clave (ej: 'user_name').
        """
        clean_key = key.strip().lower()
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, category, key, content, importance, created_at, updated_at "
                "FROM user_memories WHERE key = ?;",
                (clean_key,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def search_memories(
        self,
        query: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Busca recuerdos persistentes aplicando filtros por categoría y coincidencia de texto.
        - Qué es: Motor de búsqueda relacional de memoria episódica/semántica.
        - Para qué se usa: Encontrar memorias relevantes cuando el usuario o Halibel indagan sobre un tema.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if category and category.strip():
            conditions.append("category = ?")
            params.append(category.strip().lower())

        if query and query.strip():
            conditions.append("(content LIKE ? OR key LIKE ?)")
            wildcard = f"%{query.strip()}%"
            params.extend([wildcard, wildcard])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT id, category, key, content, importance, created_at, updated_at
            FROM user_memories
            {where_clause}
            ORDER BY importance DESC, updated_at DESC
            LIMIT ?;
        """
        params.append(max(1, min(100, limit)))

        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def delete_memory(self, memory_id: int) -> bool:
        """
        Elimina un recuerdo específico por su identificador numérico.
        """
        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_memories WHERE id = ?;", (memory_id,))
            return cursor.rowcount > 0

    def delete_memory_by_key(self, key: str) -> bool:
        """
        Elimina un recuerdo específico por su clave alfanumérica.
        """
        clean_key = key.strip().lower()
        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_memories WHERE key = ?;", (clean_key,))
            return cursor.rowcount > 0

    def get_all_memories(self, category: str | None = None) -> list[dict[str, Any]]:
        """
        Retorna la lista completa de recuerdos registrados, opcionalmente filtrados por categoría.
        """
        sql = (
            "SELECT id, category, key, content, importance, created_at, updated_at FROM user_memories "
            + ("WHERE category = ? " if category else "")
            + "ORDER BY category ASC, importance DESC, updated_at DESC;"
        )
        params = (category.strip().lower(),) if category else ()
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_profile_context(self, max_items: int = 20) -> str:
        """
        Sintetiza los recuerdos y preferencias del usuario en un bloque de texto conciso
        respetando un presupuesto máximo total para el System Instruction.
        - Qué es: Formateador de contexto cognitivo a largo plazo.
        - Para qué se usa: Proporcionar a Gemini conocimiento inmediato de la identidad, gustos
          y directivas del usuario sin disparar el consumo de tokens base.
        """
        memories = self.search_memories(limit=max_items)
        if not memories:
            return ""

        lines = ["[Perfil y Recuerdos del Usuario]:"]
        total_chars = len(lines[0])

        for m in memories:
            tag = f"({m['key']}) " if m.get("key") else ""
            entry_line = f"- [{m['category']}] {tag}{m['content']}"
            if total_chars + len(entry_line) + 1 > MAX_PROFILE_CONTEXT_CHARS:
                break
            lines.append(entry_line)
            total_chars += len(entry_line) + 1

        return "\n".join(lines) if len(lines) > 1 else ""

    # -------------------------------------------------------------------------
    # Métodos de Persistencia de Conversaciones y Sesiones de Chat
    # -------------------------------------------------------------------------

    def create_or_get_session(self, session_id: str, title: str = "Conversación principal") -> dict[str, Any]:
        """
        Garantiza la existencia de una sesión de chat en la base de datos local.
        """
        now = datetime.now().isoformat()
        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_sessions (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                RETURNING id, title, created_at, updated_at;
                """,
                (session_id, title, now, now),
            )
            row = cursor.fetchone()
            return dict(row)

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Persiste un mensaje (usuario, asistente o sistema) dentro del historial de una sesión
        en una única transacción atómica.
        - Qué es: Operación de inserción histórica en `chat_messages`.
        - Para qué se usa: Guardar turnos conversacionales completos sin duplicar conexiones SQLite.
        """
        now = datetime.now().isoformat()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            # Asegurar la sesión y actualizar su timestamp en la misma transacción
            cursor.execute(
                """
                INSERT INTO chat_sessions (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at;
                """,
                (session_id, "Conversación principal", now, now),
            )
            cursor.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata, timestamp)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id, session_id, role, content, metadata, timestamp;
                """,
                (session_id, role, content, meta_json, now),
            )
            row = cursor.fetchone()
            result = dict(row)
            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except Exception:  # noqa: BLE001, S110
                    pass
            return result

    def get_session_messages(
        self,
        session_id: str,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Recupera mensajes de una sesión en orden cronológico ascendente con soporte de
        paginación progresiva mediante cursor (`before_id`).
        - Qué es: Consulta cronológica paginada de historial conversacional.
        - Para qué se usa:
            * Sin `before_id`: devuelve los `limit` mensajes más recientes.
            * Con `before_id`: devuelve los `limit` mensajes inmediatamente anteriores a ese ID
              (ideal para carga infinita al hacer scroll hacia arriba en el chat).
        """
        safe_limit = max(1, min(200, limit))

        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            if before_id is not None:
                cursor.execute(
                    """
                    SELECT id, session_id, role, content, metadata, timestamp
                    FROM (
                        SELECT id, session_id, role, content, metadata, timestamp
                        FROM chat_messages
                        WHERE session_id = ? AND id < ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC;
                    """,
                    (session_id, before_id, safe_limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, session_id, role, content, metadata, timestamp
                    FROM (
                        SELECT id, session_id, role, content, metadata, timestamp
                        FROM chat_messages
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC;
                    """,
                    (session_id, safe_limit),
                )

            messages = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get("metadata"):
                    try:
                        item["metadata"] = json.loads(item["metadata"])
                    except Exception:  # noqa: BLE001, S110
                        pass
                messages.append(item)
            return messages

    def clear_session_messages(self, session_id: str) -> bool:
        """
        Borra todos los mensajes de una sesión conservando el registro de la sesión.
        """
        with closing(self._get_connection()) as conn, conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE session_id = ?;", (session_id,))
            return True

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """
        Lista las sesiones conversacionales registradas ordenadas por última actividad.
        """
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM chat_sessions
                ORDER BY updated_at DESC
                LIMIT ?;
                """,
                (max(1, min(100, limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]


# Instancia singleton accesible globalmente en el backend
memory_service = MemoryService()
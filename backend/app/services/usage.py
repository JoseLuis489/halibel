import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Ubicación unificada de la base de datos SQLite compartida con memory.py:
# - En desarrollo: backend/data/halibel.db
# - En producción empaquetada (PyInstaller): %APPDATA%/Halibel/halibel.db (settings.USER_DATA_DIR)
_DEV_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = (
    settings.USER_DATA_DIR / "halibel.db"
    if getattr(sys, "frozen", False)
    else _DEV_DATA_DIR / "halibel.db"
)

# Precios oficiales por cada 1,000,000 tokens en USD (Google Gemini API)
PRICING_PER_1M: dict[str, dict[str, float]] = {
    "gemini-3.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-3.8-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
}

DEFAULT_RATES = {"input": 0.075, "output": 0.30}


class UsageService:
    """
    Servicio de registro y cálculo de costos de uso de Inteligencia Artificial.
    - Qué es: Gestor de persistencia en SQLite local optimizado con WAL para auditoría de tokens.
    - Para qué se usa: Almacenar cada interacción con el modelo, calcular el costo en dólares
      y alimentar el dashboard privado de monitoreo sin bloquear lecturas/escrituras concurrentes.
    - Qué conecta: Comparte `halibel.db` con `memory_service`, recibe llamadas desde `llm_service`
      y provee métricas al panel web en `usage.py`.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """
        Inicializa el servicio asegurando que la base de datos, el modo WAL y la tabla existan.
        Acepta `db_path` opcional para facilitar pruebas automatizadas aisladas.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_db()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Fábrica centralizada de conexiones SQLite configuradas para alta concurrencia.
        - Qué es: Helper interno de conexión con pragmas de rendimiento y resiliencia.
        - Para qué se usa: Aplicar `busy_timeout` y `synchronous=NORMAL` en cada sesión abierta,
          evitando errores de `database is locked` ante lecturas/escrituras simultáneas.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Configuraciones por sesión (deben ejecutarse en cada nueva conexión)
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _ensure_db(self) -> None:
        """
        Crea el directorio, activa el modo WAL y crea la tabla `usage_logs` si no existen.
        - Qué es: Función de inicialización DDL y configuración de motor en SQLite.
        - Para qué se usa: Habilitar lecturas y escrituras simultáneas (Write-Ahead Logging)
          y garantizar que el esquema exista sin scripts manuales.
        - Qué conecta: Crea o abre el archivo físico unificado `halibel.db`.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # `closing()` garantiza `conn.close()` al salir; `with conn:` gestiona commit/rollback
            with closing(self._get_connection()) as conn, conn:
                # WAL es persistente en el archivo .db: permite leer mientras se escribe
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS usage_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        model TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL,
                        candidates_tokens INTEGER NOT NULL,
                        total_tokens INTEGER NOT NULL,
                        cost_usd REAL NOT NULL
                    )
                """)
                # Índice en timestamp para acelerar futuras consultas por rango de fechas
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_logs_timestamp
                    ON usage_logs (timestamp DESC)
                """)
        except sqlite3.Error:
            logger.exception("Error crítico al inicializar la base de datos SQLite en %s", self.db_path)

    def calculate_cost(self, model: str, prompt_tokens: int, candidates_tokens: int) -> float:
        """
        Calcula el costo estimado de una llamada en dólares estadounidenses.
        - Qué es: Función matemática de tarificación según los precios por millón de tokens.
        - Para qué se usa: Estimar con precisión de 6 decimales el gasto de entrada y salida.
        - Qué conecta: Utiliza la tabla `PRICING_PER_1M` y los tokens devueltos por Google.
        """
        rates = PRICING_PER_1M.get(model)
        if rates is None:
            logger.debug("Modelo '%s' sin tarifa explícita en PRICING_PER_1M; usando tarifa base.", model)
            rates = DEFAULT_RATES

        input_cost = (prompt_tokens / 1_000_000) * rates["input"]
        output_cost = (candidates_tokens / 1_000_000) * rates["output"]
        return round(input_cost + output_cost, 6)

    def log_usage(self, model: str, prompt_tokens: int, candidates_tokens: int) -> float:
        """
        Inserta un nuevo registro de consumo en la base de datos SQLite de forma segura.
        - Qué es: Operación de escritura (INSERT) transaccional con cierre explícito.
        - Para qué se usa: Guardar permanentemente la fecha, modelo, tokens y costo de una respuesta.
        - Qué conecta: Llamada por `llm_service` al terminar de procesar un mensaje del usuario.
        """
        total_tokens = prompt_tokens + candidates_tokens
        cost_usd = self.calculate_cost(model, prompt_tokens, candidates_tokens)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute(
                    """
                    INSERT INTO usage_logs (
                        timestamp, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now_str, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd),
                )
        except sqlite3.Error:
            logger.exception("No se pudo registrar el uso de tokens en SQLite (modelo=%s).", model)

        return cost_usd

    def get_stats(self) -> dict[str, Any]:
        """
        Recupera las métricas acumuladas y las últimas 15 solicitudes con degradación elegante.
        - Qué es: Consulta agregada (SUM, COUNT) y listado paginado con respaldo ante fallos.
        - Para qué se usa: Servir la información requerida por el Dashboard de Monitoreo sin caer en HTTP 500.
        - Qué conecta: Conecta la base de datos con los endpoints `/api/v1/usage/stats` y `/admin/usage`.
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()

                # Totales agregados acumulados
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_requests,
                        COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                        COALESCE(SUM(candidates_tokens), 0) as total_candidates_tokens,
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                    FROM usage_logs
                """)
                row = cursor.fetchone()

                # Últimos 15 registros para la tabla de historial
                cursor.execute("""
                    SELECT id, timestamp, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd
                    FROM usage_logs
                    ORDER BY id DESC
                    LIMIT 15
                """)
                recent_logs = [dict(r) for r in cursor.fetchall()]

                return {
                    "total_requests": row["total_requests"] if row else 0,
                    "total_prompt_tokens": row["total_prompt_tokens"] if row else 0,
                    "total_candidates_tokens": row["total_candidates_tokens"] if row else 0,
                    "total_tokens": row["total_tokens"] if row else 0,
                    "total_cost_usd": round(row["total_cost_usd"], 4) if row else 0.0,
                    "recent_logs": recent_logs,
                    "current_model": settings.GEMINI_MODEL,
                    "db_status": "ok",
                }

        except sqlite3.Error:
            # Degradación elegante: si la BD está ocupada o bloqueada, devolvemos métricas en cero
            # para que la interfaz del dashboard siga renderizando sin lanzar HTTP 500
            logger.exception("Error leyendo estadísticas de SQLite; devolviendo estado de respaldo.")
            return {
                "total_requests": 0,
                "total_prompt_tokens": 0,
                "total_candidates_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "recent_logs": [],
                "current_model": settings.GEMINI_MODEL,
                "db_status": "unavailable",
            }


# Instancia singleton accesible en toda la aplicación
usage_service = UsageService()
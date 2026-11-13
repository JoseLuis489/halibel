import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

# Ubicación del archivo de base de datos SQLite: backend/data/halibel.db
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "halibel.db"

# Precios oficiales por cada 1,000,000 tokens en USD (Google Gemini API)
PRICING_PER_1M = {
    "gemini-3.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-3.8-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
}


class UsageService:
    """
    Servicio de registro y cálculo de costos de uso de Inteligencia Artificial.
    - Qué es: Gestor de persistencia en SQLite local para auditoría de tokens.
    - Para qué se usa: Almacenar cada interacción con el modelo, calcular el costo en dólares
      y alimentar el dashboard privado de monitoreo.
    - Qué conecta: Recibe llamadas desde `llm_service.stream_message` y provee datos al endpoint
      y panel web en `usage.py`.
    """

    def __init__(self):
        """Inicializa el servicio asegurando que la base de datos y la tabla existan."""
        self._ensure_db()

    def _ensure_db(self):
        """
        Crea el directorio y la tabla `usage_logs` si no existen previamente.
        - Qué es: Función de inicialización DDL en SQLite.
        - Para qué se usa: Garantizar que la base de datos esté lista sin requerir scripts manuales.
        - Qué conecta: Crea el archivo físico `halibel.db` en el directorio `backend/data/`.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
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
            conn.commit()

    def calculate_cost(self, model: str, prompt_tokens: int, candidates_tokens: int) -> float:
        """
        Calcula el costo estimado de una llamada en dólares estadounidenses.
        - Qué es: Función matemática de tarificación según los precios por millón de tokens.
        - Para qué se usa: Estimar con precisión de 6 decimales el gasto de entrada y salida.
        - Qué conecta: Utiliza la tabla `PRICING_PER_1M` y los tokens devueltos por Google.
        """
        rates = PRICING_PER_1M.get(model, {"input": 0.075, "output": 0.30})
        input_cost = (prompt_tokens / 1_000_000) * rates["input"]
        output_cost = (candidates_tokens / 1_000_000) * rates["output"]
        return round(input_cost + output_cost, 6)

    def log_usage(self, model: str, prompt_tokens: int, candidates_tokens: int) -> float:
        """
        Inserta un nuevo registro de consumo en la base de datos SQLite.
        - Qué es: Operación de escritura (INSERT) transaccional.
        - Para qué se usa: Guardar permanentemente la fecha, modelo, tokens y costo de una respuesta.
        - Qué conecta: Llamada por `llm_service` al terminar de procesar un mensaje del usuario.
        """
        total_tokens = prompt_tokens + candidates_tokens
        cost_usd = self.calculate_cost(model, prompt_tokens, candidates_tokens)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO usage_logs (timestamp, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, model, prompt_tokens, candidates_tokens, total_tokens, cost_usd))
            conn.commit()

        return cost_usd

    def get_stats(self) -> dict[str, Any]:
        """
        Recupera las métricas acumuladas y las últimas 15 solicitudes.
        - Qué es: Consulta agregada (SUM, COUNT) y listado paginado.
        - Para qué se usa: Servir la información requerida por el Dashboard de Monitoreo.
        - Qué conecta: Conecta la base de datos con los endpoints `/api/v1/usage/stats` y `/admin/usage`.
        """
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
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
            recent_rows = cursor.fetchall()
            recent_logs = [dict(r) for r in recent_rows]

            return {
                "total_requests": row["total_requests"],
                "total_prompt_tokens": row["total_prompt_tokens"],
                "total_candidates_tokens": row["total_candidates_tokens"],
                "total_tokens": row["total_tokens"],
                "total_cost_usd": round(row["total_cost_usd"], 4),
                "recent_logs": recent_logs,
                "current_model": settings.GEMINI_MODEL
            }


# Instancia singleton accesible en toda la aplicación
usage_service = UsageService()

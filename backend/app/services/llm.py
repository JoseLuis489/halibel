import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import settings
from app.services.usage import usage_service

logger = logging.getLogger(__name__)


class LLMService:
    """
    Servicio de integración con el modelo de lenguaje (Gemini API).
    - Qué es: Capa adaptadora entre el backend y el SDK oficial google-genai.
    - Para qué se usa: Gestionar la autenticación, ensamblar el contexto (persona + fecha/hora + historial),
      transmitir respuestas en streaming continuo y registrar el consumo de tokens.
    - Qué conecta: Se comunica externamente con la API de Google y sirve internamente a los endpoints
      de chat (chat.py) y al monitor de métricas (usage_service).
    """

    def __init__(self) -> None:
        """Inicializa el cliente en modo perezoso (lazy loading) para evitar fallos si la API key aún no carga."""
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        """
        Instancia autenticada del cliente de Google GenAI.
        - Qué es: Propiedad con inicialización bajo demanda.
        - Para qué se usa: Reutilizar una única conexión HTTP/2 autenticada con la API de Google.
        - Qué conecta: Lee `settings.GEMINI_API_KEY` y crea el objeto `genai.Client`.
        """
        if self._client is None:
            if not settings.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY no está configurada en el archivo .env")
            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        return self._client

    async def stream_message(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Genera y transmite la respuesta de Halibel fragmento a fragmento (tokens) en tiempo real.
        - Qué es: Generador asíncrono (AsyncGenerator) de streaming.
        - Para qué se usa:
            1. Inyectar en el System Instruction la personalidad y la fecha/hora exacta del sistema operativo.
            2. Construir la lista de contenidos con el historial multi-turno previo.
            3. Llamar a `client.aio.models.generate_content_stream` de forma 100% asíncrona.
            4. Emitir cada fragmento de texto (`chunk.text`) hacia la interfaz.
            5. Capturar los metadatos de uso (`usage_metadata`) al terminar y guardarlos en SQLite.
        - Qué conecta: Conecta la API de Gemini con el canal WebSocket de `chat.py` y con `usage_service.log_usage`.
        """
        if not settings.GEMINI_API_KEY:
            yield (
                "Hola. Parece que aún no has configurado tu GEMINI_API_KEY en el archivo backend/.env. "
                "Por favor agrégala para que podamos conversar."
            )
            return

        # Contexto dinámico de fecha y hora local del sistema
        now = datetime.now()
        system_instruction = (
            f"{settings.HALIBEL_PERSONA}\n\n"
            f"[Contexto del sistema del usuario]:\n"
            f"- Fecha y hora actual del equipo: {now.strftime('%d-%m-%Y %H:%M:%S')}."
        )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
        )

        # Construcción de la conversación con el historial previo
        contents: list[types.Content | str] = []
        if history:
            for item in history:
                role = "user" if item.get("role") == "user" else "model"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=item.get("content", ""))],
                    )
                )
        contents.append(user_message)

        final_usage = None

        try:
            # Usamos .aio para que tanto la conexión como la lectura de cada chunk sean 100% no bloqueantes
            stream_response = await self.client.aio.models.generate_content_stream(
                model=settings.GEMINI_MODEL,
                contents=contents,
                config=config,
            )

            async for chunk in stream_response:
                if chunk.usage_metadata:
                    final_usage = chunk.usage_metadata
                if chunk.text:
                    yield chunk.text

        except asyncio.CancelledError:
            # Si el usuario cierra la pestaña o detiene el mensaje, registramos y propagamos la cancelación limpia
            logger.info("Streaming cancelado por el cliente (WebSocket desconectado o abortado).")
            raise

        except APIError as e:
            # Errores HTTP explícitos devueltos por Google (429 cuota, 400 bad request, 503 caído, etc.)
            logger.error("Error de API Gemini (HTTP %s): %s", e.code, e.message, exc_info=True)  # noqa: G201

            if e.code == 429:
                yield "\n[Halibel alcanzó el límite de peticiones por minuto. Espera unos segundos e intenta de nuevo.]"
            elif e.code in (400, 403):
                yield "\n[Error de autenticación o configuración con el servicio de IA. Revisa tu API Key.]"
            else:
                yield "\n[El servicio de IA de Google no está disponible temporalmente. Intenta más tarde.]"

        except ValueError as e:
            # El SDK lanza ValueError si intentas leer `chunk.text` cuando la respuesta fue bloqueada por seguridad
            logger.warning("Respuesta bloqueada por filtros de seguridad o contenido vacío: %s", e)
            yield "\n[No pude completar la respuesta debido a restricciones de seguridad en el contenido.]"

        except Exception:
            # Red de seguridad final: registra el traceback completo en consola/archivo sin exponerlo al frontend
            logger.exception("Error inesperado durante el streaming con Halibel")
            yield "\n[Ocurrió un error interno inesperado al procesar tu mensaje.]"
            return  # Evitamos intentar registrar métricas si el flujo falló a mitad

        # Aislamos el guardado en SQLite: si la BD falla o está bloqueada, el usuario NO ve un error en el chat
        if final_usage:
            try:
                p_tokens = final_usage.prompt_token_count or 0
                c_tokens = final_usage.candidates_token_count or 0
                usage_service.log_usage(settings.GEMINI_MODEL, p_tokens, c_tokens)
            except Exception:
                logger.exception("No se pudieron registrar los tokens en SQLite, pero la respuesta se entregó.")


# Instancia compartida del servicio
llm_service = LLMService()
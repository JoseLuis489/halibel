import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime

from google import genai
from google.genai import types

from app.core.config import settings
from app.services.usage import usage_service


class LLMService:
    """
    Servicio de integración con el modelo de lenguaje (Gemini API).
    - Qué es: Capa adaptadora entre el backend y el SDK oficial google-genai.
    - Para qué se usa: Gestionar la autenticación, ensamblar el contexto (persona + fecha/hora + historial),
      transmitir respuestas en streaming continuo y registrar el consumo de tokens.
    - Qué conecta: Se comunica externamente con la API de Google y sirve internamente a los endpoints
      de chat (chat.py) y al monitor de métricas (usage_service).
    """

    def __init__(self):
        """Inicializa el cliente en modo perezoso (lazy loading) para evitar fallos si la API key aún no carga."""
        self._client = None
    
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
        history: list[dict[str, str]] | None = None
    ) -> AsyncGenerator[str, None]:
        """
        Genera y transmite la respuesta de Halibel fragmento a fragmento (tokens) en tiempo real.
        - Qué es: Generador asíncrono (AsyncGenerator) de streaming.
        - Para qué se usa:
            1. Inyectar en el System Instruction la personalidad y la fecha/hora exacta del sistema operativo.
            2. Construir la lista de contenidos con el historial multi-turno previo.
            3. Llamar a `client.models.generate_content_stream` de forma no bloqueante.
            4. Emitir cada fragmento de texto (`chunk.text`) hacia la interfaz.
            5. Capturar los metadatos de uso (`usage_metadata`) al terminar y guardarlos en SQLite.
        - Qué conecta: Conecta la API de Gemini con el canal WebSocket de `chat.py` y con `usage_service.log_usage`.
        """
        if not settings.GEMINI_API_KEY:
            yield "Hola. Parece que aún no has configurado tu GEMINI_API_KEY en el archivo backend/.env. Por favor agrégala para que podamos conversar."
            return

        try:
            # Contexto dinámico de fecha y hora local del sistema
            now = datetime.now()
            system_instruction = (
                f"{settings.HALIBEL_PERSONA}\n\n"
                f"[Contexto del sistema del usuario]:\n"
                f"- Fecha y hora actual del equipo: {now.strftime('%d-%m-%Y %H:%M:%S')}."
            )

            # Configuración de generación para el modelo
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
            )

            # Construcción de la conversación con el historial previo
            contents = []
            if history:
                for item in history:
                    role = "user" if item.get("role") == "user" else "model"
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=item.get("content", ""))]
                        )
                    )
            contents.append(user_message)

            # Ejecutar el streaming en un hilo separado para no bloquear el bucle de asyncio
            loop = asyncio.get_running_loop()

            def sync_stream():
                return self.client.models.generate_content_stream(
                    model=settings.GEMINI_MODEL,
                    contents=contents,
                    config=config
                )

            stream_response = await loop.run_in_executor(None, sync_stream)

            final_usage = None
            for chunk in stream_response:
                if chunk.usage_metadata:
                    final_usage = chunk.usage_metadata
                if chunk.text:
                    yield chunk.text

            # Registrar métricas en SQLite al finalizar el flujo
            if final_usage:
                p_tokens = final_usage.prompt_token_count or 0
                c_tokens = final_usage.candidates_token_count or 0
                usage_service.log_usage(settings.GEMINI_MODEL, p_tokens, c_tokens)

        except Exception as e:  # noqa: BLE001
            yield f"\n[Error de comunicación con Halibel: {e!s}]"


# Instancia compartida del servicio
llm_service = LLMService()

import asyncio
from collections.abc import AsyncGenerator

from google import genai
from google.genai import types

from app.core.config import settings


class LLMService:
    def __init__(self):
        self._client = None
    
    @property
    def client(self) -> genai.Client:
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
        Transmite la respuesta de Gemini en tiempo real (token streaming).
        """
        if not settings.GEMINI_API_KEY:
            yield "Hola. Parece que aún no has configurado tu GEMINI_API_KEY en el archivo backend/.env. Por favor agrégala para que podamos conversar."
            return

        try:
            # Prepare configuration with system instructions
            config = types.GenerateContentConfig(
                system_instruction=settings.HALIBEL_PERSONA,
                temperature=0.7,
            )

            # Build contents
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

            # Stream generation in a separate thread to keep asyncio loop responsive
            loop = asyncio.get_running_loop()

            def sync_stream():
                return self.client.models.generate_content_stream(
                    model=settings.GEMINI_MODEL,
                    contents=contents,
                    config=config
                )

            stream_response = await loop.run_in_executor(None, sync_stream)

            for chunk in stream_response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:  # noqa: BLE001
            yield f"\n[Error de comunicación con Halibel: {e!s}]"

llm_service = LLMService()

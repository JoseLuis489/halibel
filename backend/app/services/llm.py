import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import settings
from app.services.usage import usage_service
from app.tools.registry import AVAILABLE_TOOLS, execute_tool, is_destructive_tool

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
        on_approval_request: Callable[[str, str, dict[str, Any], str], Awaitable[bool]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Genera y transmite la respuesta de Halibel fragmento a fragmento en tiempo real,
        orquestando de forma autónoma la ejecución de herramientas seguras y la supervisión
        humana (Human-in-the-Loop) para herramientas destructivas.
        - Qué es: Bucle de razonamiento y ejecución de agente (Agent Loop) con streaming asíncrono.
        - Para qué se usa:
            1. Inyectar en el System Instruction la personalidad y la fecha/hora exacta del sistema operativo.
            2. Construir la lista de contenidos con el historial previo y las herramientas registradas.
            3. Detectar llamadas a funciones (`function_calls`) emitidas por Gemini.
            4. Ejecutar automáticamente herramientas de solo lectura (`list_directory`, `read_file`).
            5. Pausar y solicitar confirmación interactiva para herramientas destructivas (`safe_delete_file`).
            6. Reinyectar los resultados en el contexto y continuar el streaming de la respuesta final.
            7. Acumular y registrar el consumo de tokens en SQLite al finalizar.
        - Qué conecta: Conecta Gemini API con el catálogo de herramientas (`app.tools.registry`),
          el WebSocket (`chat.py`) y el monitor de uso (`usage_service`).
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
            tools=AVAILABLE_TOOLS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # Construcción de la conversación con el historial previo
        contents: list[types.Content] = []
        if history:
            for item in history:
                role = "user" if item.get("role") == "user" else "model"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=item.get("content", ""))],
                    )
                )
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_message)],
            )
        )

        total_prompt_tokens = 0
        total_candidate_tokens = 0
        max_tool_iterations = 5

        try:
            for _ in range(max_tool_iterations):
                # Usamos .aio para streaming no bloqueante con Gemini
                stream_response = await self.client.aio.models.generate_content_stream(
                    model=settings.GEMINI_MODEL,
                    contents=contents,
                    config=config,
                )

                function_calls: list[types.FunctionCall] = []
                model_parts: list[types.Part] = []
                turn_usage = None

                async for chunk in stream_response:
                    if chunk.usage_metadata:
                        turn_usage = chunk.usage_metadata

                    if chunk.function_calls:
                        function_calls.extend(chunk.function_calls)

                    if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                        model_parts.extend(chunk.candidates[0].content.parts)

                    if chunk.text:
                        yield chunk.text

                # Acumular tokens consumidos en este turno
                if turn_usage:
                    total_prompt_tokens += (turn_usage.prompt_token_count or 0)
                    total_candidate_tokens += (turn_usage.candidates_token_count or 0)

                # Si no hubo llamadas a herramientas, la respuesta conversacional terminó
                if not function_calls:
                    break

                # Registrar la llamada que hizo el modelo antes de inyectar las respuestas
                contents.append(types.Content(role="model", parts=model_parts))
                response_parts: list[types.Part] = []

                for call in function_calls:
                    call_id = call.id or f"call_{uuid.uuid4().hex[:8]}"
                    call_name = call.name
                    call_args = call.args or {}

                    if is_destructive_tool(call_name):
                        target_path = str(call_args.get("path", "archivo"))
                        desc = f"Halibel solicita enviar el archivo '{target_path}' a la papelera de reciclaje."
                        yield f"\n⚠️ *[Solicitando autorización para: {call_name}]*\n"

                        approved = False
                        if on_approval_request:
                            try:
                                approved = await on_approval_request(call_id, call_name, call_args, desc)
                            except Exception as e:  # noqa: BLE001
                                logger.error("Error esperando autorización del usuario: %s", e)
                                approved = False
                        else:
                            logger.warning("Acción destructiva rechazada: no se proporcionó callback de aprobación.")

                        if approved:
                            yield f"\n*[Acción autorizada por el usuario. Ejecutando {call_name}...]*\n\n"
                            result = execute_tool(call_name, call_args)
                        else:
                            yield "\n*[Acción cancelada o rechazada por el usuario.]*\n\n"
                            result = {
                                "status": "cancelled",
                                "message": f"Operación cancelada: El usuario no autorizó eliminar el archivo '{target_path}'.",
                            }
                    else:
                        # Herramienta de lectura segura
                        yield f"\n🔍 *[Consultando: {call_name}...]*\n\n"
                        result = execute_tool(call_name, call_args)

                    response_parts.append(
                        types.Part.from_function_response(
                            name=call_name,
                            response={"result": result},
                        )
                    )

                contents.append(types.Content(role="user", parts=response_parts))

        except asyncio.CancelledError:
            # Si el usuario cierra la ventana o detiene el mensaje, cancelamos limpiamente
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

        finally:
            # Aislamos el guardado en SQLite: si la BD falla, el chat NO se rompe
            if total_prompt_tokens > 0 or total_candidate_tokens > 0:
                try:
                    usage_service.log_usage(settings.GEMINI_MODEL, total_prompt_tokens, total_candidate_tokens)
                except Exception:
                    logger.exception("No se pudieron registrar los tokens en SQLite, pero la respuesta se entregó.")


# Instancia compartida del servicio
llm_service = LLMService()
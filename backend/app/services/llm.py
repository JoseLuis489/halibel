import asyncio
import base64
import logging
import os
import platform
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import settings
from app.services.memory import memory_service
from app.services.usage import usage_service
from app.tools.registry import (
    AVAILABLE_TOOLS,
    execute_tool,
    get_tool_approval_description,
    is_destructive_tool,
)

logger = logging.getLogger(__name__)

# Tiempo máximo (en segundos) permitido para que una herramienta de I/O responda
TOOL_EXECUTION_TIMEOUT_SECONDS = 15.0

# Presupuesto máximo de caracteres para el historial conversacional (~4,000 tokens aprox.)
MAX_HISTORY_TOTAL_CHARS = 16_000
MAX_SINGLE_HISTORY_MSG_CHARS = 4_000


def _build_runtime_context() -> str:
    """
    Construye el bloque de contexto dinámico del entorno local del usuario y su perfil cognitivo.
    - Qué es: Helper de introspección del sistema operativo, rutas base y memoria persistente a largo plazo.
    - Para qué se usa: Inyectar fecha/hora, SO real (Windows/Linux/Darwin), separador de rutas, directorio de
      usuario y recuerdos acumulados para que Gemini razone con rutas válidas y recuerde datos del usuario.
    """
    now = datetime.now()
    os_name = platform.system()       # Ej: 'Windows', 'Linux', 'Darwin'
    os_release = platform.release() # Ej: '10', '11', '6.8.0-...'
    home_dir = str(Path.home())
    cwd_dir = str(Path.cwd())

    lines = [
        "[Contexto del sistema del usuario]:",
        f"- Fecha y hora actual del equipo: {now.strftime('%d-%m-%Y %H:%M:%S')}.",
        f"- Sistema operativo: {os_name} {os_release} (separador de rutas: '{os.sep}').",
        f"- Directorio raíz del usuario (Home): {home_dir}",
        f"- Directorio actual de trabajo (CWD): {cwd_dir}",
        f"- Regla de rutas: Usa siempre la sintaxis nativa de {os_name} al construir o mostrar rutas.",
        "- Percepción y herramientas: Cuentas con herramientas de búsqueda web (web_search) y lectura de páginas (fetch_web_page), además de visión multimodal si el usuario envía capturas de pantalla.",
    ]

    # Inyección de recuerdos a largo plazo y preferencias registradas
    profile_memory = memory_service.get_profile_context()
    if profile_memory:
        lines.append("")
        lines.append(profile_memory)

    return "\n".join(lines)


def _trim_history_to_budget(
    history: list[dict[str, str]] | None,
    max_total_chars: int = MAX_HISTORY_TOTAL_CHARS,
    max_single_chars: int = MAX_SINGLE_HISTORY_MSG_CHARS,
) -> list[dict[str, str]]:
    """
    Poda el historial conversacional desde los mensajes más recientes hacia los más antiguos
    respetando un presupuesto estricto de caracteres para evitar disparar el consumo de tokens.
    """
    if not history:
        return []

    selected_reversed: list[dict[str, str]] = []
    accumulated_chars = 0

    for item in reversed(history):
        raw_content = item.get("content", "")
        if not raw_content.strip():
            continue

        # Truncar un mensaje individual si fue demasiado largo (ej. volcado extenso de código)
        if len(raw_content) > max_single_chars:
            content = (
                raw_content[:max_single_chars]
                + "\n...[contenido histórico truncado por presupuesto de contexto]..."
            )
        else:
            content = raw_content

        if accumulated_chars + len(content) > max_total_chars:
            # Si ya incluimos al menos un turno reciente y el siguiente desborda el presupuesto, paramos
            if selected_reversed:
                break
            # Si incluso el mensaje más reciente excede el presupuesto global, lo recortamos al límite
            remaining = max(500, max_total_chars - accumulated_chars)
            content = content[:remaining] + "\n...[truncado]..."

        selected_reversed.append({
            "role": item.get("role", "user"),
            "content": content,
        })
        accumulated_chars += len(content)

    selected_reversed.reverse()
    return selected_reversed


async def _execute_tool_safely(call_name: str, call_args: dict[str, Any]) -> dict[str, Any]:
    """
    Ejecuta una herramienta en un hilo separado con límite de tiempo estricto (`wait_for`).
    Evita que unidades de red congeladas o archivos bloqueados por el kernel cuelguen el agente.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(execute_tool, call_name, call_args),
            timeout=TOOL_EXECUTION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error(
            "Timeout ejecutando herramienta '%s' tras %.1fs (args=%s)",
            call_name,
            TOOL_EXECUTION_TIMEOUT_SECONDS,
            call_args,
        )
        return {
            "status": "error",
            "error": (
                f"La operación '{call_name}' excedió el tiempo límite de "
                f"{int(TOOL_EXECUTION_TIMEOUT_SECONDS)} segundos (posible disco lento o recurso bloqueado)."
            ),
        }


class LLMService:
    """
    Servicio de integración con el modelo de lenguaje (Gemini API).
    - Qué es: Capa adaptadora entre el backend y el SDK oficial google-genai.
    - Para qué se usa: Gestionar la autenticación, ensamblar el contexto (persona + entorno SO + historial),
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
        image_data: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Genera y transmite la respuesta de Halibel fragmento a fragmento en tiempo real,
        orquestando de forma autónoma la ejecución de herramientas seguras y la supervisión
        humana (Human-in-the-Loop) para herramientas destructivas o sensibles.
        """
        if not settings.GEMINI_API_KEY:
            yield (
                f"Hola. Parece que aún no has configurado tu GEMINI_API_KEY en `{settings.ACTIVE_ENV_FILE}`. "
                "Por favor agrégala para que podamos conversar."
            )
            return

        # Contexto dinámico: personalidad + fecha/hora + arquitectura real del SO + memoria
        system_instruction = f"{settings.HALIBEL_PERSONA}\n\n{_build_runtime_context()}"

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            tools=AVAILABLE_TOOLS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # Construcción de la conversación aplicando presupuesto de contexto al historial
        trimmed_history = _trim_history_to_budget(history)
        contents: list[types.Content] = []
        for item in trimmed_history:
            role = "user" if item.get("role") == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=item.get("content", ""))],
                )
            )
        user_parts = [types.Part.from_text(text=user_message)]
        if image_data and image_data.startswith("data:image/"):
            try:
                header, encoded = image_data.split(",", 1)
                mime_type = header.split(";")[0].replace("data:", "")
                image_bytes = base64.b64decode(encoded)
                user_parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
                logger.info("Imagen multimodal procesada correctamente (%s, %d bytes)", mime_type, len(image_bytes))
            except Exception as e:  # noqa: BLE001
                logger.warning("No se pudo decodificar la imagen adjunta: %s", e)

        contents.append(
            types.Content(
                role="user",
                parts=user_parts,
            )
        )

        total_prompt_tokens = 0
        total_candidate_tokens = 0
        max_tool_iterations = 5
        completed_naturally = False

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
                        for part in chunk.candidates[0].content.parts:
                            if getattr(part, "text", None):
                                yield part.text
                    elif not chunk.function_calls and getattr(chunk, "text", None):
                        yield chunk.text

                # Acumular tokens consumidos en este turno
                if turn_usage:
                    total_prompt_tokens += (turn_usage.prompt_token_count or 0)
                    total_candidate_tokens += (turn_usage.candidates_token_count or 0)

                # Si no hubo llamadas a herramientas, la respuesta conversacional terminó
                if not function_calls:
                    completed_naturally = True
                    break

                # Registrar la llamada que hizo el modelo antes de inyectar las respuestas
                contents.append(types.Content(role="model", parts=model_parts))
                response_parts: list[types.Part] = []

                for call in function_calls:
                    call_id = call.id or f"call_{uuid.uuid4().hex[:8]}"
                    call_name = call.name or "unknown_tool"
                    call_args = dict(call.args) if call.args else {}

                    if is_destructive_tool(call_name, call_args):
                        target_path = str(call_args.get("path", "recurso"))
                        desc = get_tool_approval_description(call_name, call_args)
                        yield f"\n⚠️ *[Solicitando autorización para: {call_name}]*\n"

                        approved = False
                        if on_approval_request:
                            try:
                                approved = await on_approval_request(call_id, call_name, call_args, desc)
                            except TimeoutError:
                                logger.warning("Tiempo de espera agotado (timeout) para aprobar %s", call_name)
                                approved = False
                            except Exception as e:  # noqa: BLE001
                                logger.error("Error esperando autorización del usuario: %s", e)
                                approved = False
                        else:
                            logger.warning("Acción destructiva rechazada: no se proporcionó callback de aprobación.")

                        if approved:
                            yield f"\n*[Acción autorizada por el usuario. Ejecutando {call_name}...]*\n\n"
                            # Si el usuario aprobó explícitamente un write_file sensible, habilitamos el flag interno
                            if call_name == "write_file":
                                call_args["allow_executable"] = True
                            result = await _execute_tool_safely(call_name, call_args)
                        else:
                            yield "\n*[Acción cancelada o rechazada por el usuario.]*\n\n"
                            result = {
                                "status": "cancelled",
                                "message": f"Operación cancelada: El usuario no autorizó la ejecución de '{call_name}' para '{target_path}'.",
                            }
                    else:
                        # Herramienta segura ejecutada en segundo plano con timeout preventivo
                        if "web_search" in call_name or "search" in call_name:
                            yield f"\n🌐 *[Buscando en internet: {call_name}...]*\n\n"
                        elif "fetch" in call_name or "page" in call_name:
                            yield f"\n📄 *[Leyendo página web: {call_name}...]*\n\n"
                        elif "read" in call_name or "list" in call_name or "recall" in call_name:
                            yield f"\n🔍 *[Consultando: {call_name}...]*\n\n"
                        elif "memory" in call_name:
                            yield f"\n🧠 *[Actualizando memoria: {call_name}...]*\n\n"
                        else:
                            yield f"\n⚙️ *[Ejecutando: {call_name}...]*\n\n"
                        result = await _execute_tool_safely(call_name, call_args)

                    response_parts.append(
                        types.Part.from_function_response(
                            name=call_name,
                            response={"result": result},
                        )
                    )

                contents.append(types.Content(role="user", parts=response_parts))

            # Aviso explícito si el agente agotó los 5 pasos sin emitir respuesta final
            if not completed_naturally:
                logger.warning("El agente alcanzó el límite máximo de %d iteraciones de herramientas.", max_tool_iterations)
                yield "\n\n*[Alcancé el límite máximo de pasos autónomos para esta solicitud. Pídeme continuar si necesitas más detalles.]*"

        except asyncio.CancelledError:
            logger.info("Streaming cancelado por el cliente (WebSocket desconectado o abortado).")
            raise

        except APIError as e:
            logger.error("Error de API Gemini (HTTP %s): %s", e.code, e.message, exc_info=True)  # noqa: G201

            if e.code == 429:
                yield "\n[Halibel alcanzó el límite de peticiones por minuto. Espera unos segundos e intenta de nuevo.]"
            elif e.code in (400, 403):
                yield "\n[Error de autenticación o configuración con el servicio de IA. Revisa tu API Key.]"
            else:
                yield "\n[El servicio de IA de Google no está disponible temporalmente. Intenta más tarde.]"

        except ValueError as e:
            logger.warning("Respuesta bloqueada por filtros de seguridad o contenido vacío: %s", e)
            yield "\n[No pude completar la respuesta debido a restricciones de seguridad en el contenido.]"

        except Exception:
            logger.exception("Error inesperado durante el streaming con Halibel")
            yield "\n[Ocurrió un error interno inesperado al procesar tu mensaje.]"
            return

        finally:
            # Aislamos el guardado en SQLite: si la BD falla, el chat NO se rompe
            if total_prompt_tokens > 0 or total_candidate_tokens > 0:
                try:
                    usage_service.log_usage(settings.GEMINI_MODEL, total_prompt_tokens, total_candidate_tokens)
                except Exception:
                    logger.exception("No se pudieron registrar los tokens en SQLite, pero la respuesta se entregó.")


# Instancia compartida del servicio
llm_service = LLMService()
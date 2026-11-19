import ipaddress
import logging
import re
import socket
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        DDGS = None

logger = logging.getLogger(__name__)

# Configuración de límites y seguridad para peticiones HTTP
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB límite para evitar bombas de descompresión
MAX_FETCH_CHARS_DEFAULT = 4_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 HalibelCompanion/1.0"
)


def _validate_safe_url(url: str) -> tuple[bool, str]:
    """
    Verifica que la URL sea segura y previene ataques de Server-Side Request Forgery (SSRF).
    - Qué es: Filtro de validación de esquemas y destinos de red.
    - Para qué se usa: Impedir que el modelo o un atacante solicite direcciones locales
      (localhost, 127.0.0.1, 0.0.0.0) o redes privadas internas (10.x, 192.168.x, 172.16.x).
    - Qué conecta: Invocado por `fetch_web_page` antes de abrir cualquier socket HTTP.
    """
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False, f"Protocolo no soportado '{parsed.scheme}'. Solo se admiten 'http' y 'https'."

        hostname = parsed.hostname
        if not hostname:
            return False, "La URL proporcionada no contiene un nombre de host válido."

        # Bloqueo directo de localhost
        if hostname.lower() in ("localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0", "::1"):
            return False, "Acceso a loopback / localhost denegado por seguridad."

        # Resolución DNS y verificación de rangos privados / reservados
        try:
            ip_str = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, f"Acceso a la dirección de red interna ({ip}) denegado por seguridad."
        except socket.gaierror:
            return False, f"No se pudo resolver el dominio '{hostname}' mediante DNS."

        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"Error al validar la URL: {e!s}"


def _clean_html_to_text(html: str) -> tuple[str, str]:
    """
    Convierte contenido HTML en texto estructurado limpio y legible para el LLM.
    - Qué es: Parser ligero basado en expresiones regulares y normalización Unicode.
    - Para qué se usa: Eliminar scripts, estilos, anuncios, cabeceras de navegación y
      formatear títulos y párrafos sin dependencias externas pesadas.
    - Qué conecta: Invocado por `fetch_web_page` sobre la respuesta HTML cruda.
    """
    # 1. Extraer título de la página si existe
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    title = unescape(title_match.group(1)).strip() if title_match else "Página Web"

    # 2. Descartar secciones de ruido (scripts, estilos, navegaciones, pie de página, svg)
    clean = re.sub(
        r"<(script|style|nav|header|footer|svg|noscript|form)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 3. Convertir encabezados HTML a Markdown
    clean = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n### \1\n", clean, flags=re.IGNORECASE)

    # 4. Convertir saltos de bloque (p, div, li, tr, br) a saltos de línea
    clean = re.sub(r"</(p|div|li|tr)>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<br\s*/?>", "\n", clean, flags=re.IGNORECASE)

    # 5. Descartar cualquier otra etiqueta HTML remanente
    clean = re.sub(r"<[^>]+>", " ", clean)

    # 6. Decodificar entidades HTML (&amp;, &lt;, &gt;, &quot;, &#39;, etc.)
    text = unescape(clean)

    # 7. Normalizar espacios múltiples y saltos de línea consecutivos
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return title, "\n".join(lines)


def fetch_web_page(url: str, max_chars: int = MAX_FETCH_CHARS_DEFAULT) -> dict[str, Any]:
    """
    Descarga y extrae el contenido textual legible de una página web o enlace de documentación.
    - Qué es: Herramienta de lectura web en vivo con protección anti-SSRF y limpieza de HTML.
    - Para qué se usa: Permitir a Halibel leer artículos, documentación de librerías, repositorios o noticias
      cuando el usuario le pasa un enlace (URL) o cuando necesita profundizar en un resultado de búsqueda.
    - Qué conecta: Consumida por el orquestador Function Calling en `llm_service`.
    """
    clean_url = url.strip()
    is_safe, error_msg = _validate_safe_url(clean_url)
    if not is_safe:
        logger.warning("fetch_web_page bloqueado por SSRF: %s -> %s", clean_url, error_msg)
        return {
            "status": "error",
            "url": clean_url,
            "error": error_msg,
        }

    safe_max_chars = max(500, min(15_000, int(max_chars)))

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = client.get(clean_url, headers=headers)

            if resp.status_code != 200:
                return {
                    "status": "error",
                    "url": clean_url,
                    "http_status": resp.status_code,
                    "error": f"El servidor web devolvió código HTTP {resp.status_code}.",
                }

            # Validar tamaño máximo para proteger memoria
            if len(resp.content) > MAX_RESPONSE_BYTES:
                return {
                    "status": "error",
                    "url": clean_url,
                    "error": f"La página excede el tamaño máximo permitido ({len(resp.content)} bytes).",
                }

            content_type = resp.headers.get("content-type", "").lower()
            # Si es texto plano o markdown directo
            if "text/plain" in content_type or "text/markdown" in content_type:
                title = clean_url
                text_content = resp.text
            else:
                title, text_content = _clean_html_to_text(resp.text)

            # Truncar con aviso si excede el presupuesto del contexto
            was_truncated = len(text_content) > safe_max_chars
            final_content = (
                text_content[:safe_max_chars].rstrip() + "\n\n...[Contenido truncado para respetar el contexto]..."
                if was_truncated
                else text_content
            )

            logger.info("fetch_web_page exitoso para '%s' (%d caracteres)", clean_url, len(final_content))
            return {
                "status": "success",
                "url": clean_url,
                "title": title,
                "content": final_content,
                "truncated": was_truncated,
            }

    except httpx.TimeoutException:
        logger.warning("Timeout al conectar con %s", clean_url)
        return {"status": "error", "url": clean_url, "error": "Tiempo de espera agotado al conectar con el sitio web."}
    except Exception as e:
        logger.exception("Error al obtener página web: %s", clean_url)
        return {"status": "error", "url": clean_url, "error": f"Error de conexión: {e!s}"}


def _search_wikipedia_fallback(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Búsqueda de respaldo enciclopédica y conceptual usando la API oficial de Wikipedia."""
    headers = {"User-Agent": "HalibelCompanion/1.0 (https://github.com/halibel; contact@halibel.local)"}
    # Intentar primero en español
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(
                "https://es.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "format": "json",
                    "utf8": 1,
                    "srlimit": max_results,
                },
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("query", {}).get("search", [])
                results = []
                for item in items:
                    title = item.get("title", "")
                    clean_snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
                    page_url = f"https://es.wikipedia.org/wiki/{title.replace(' ', '_')}"
                    results.append({
                        "title": title,
                        "url": page_url,
                        "snippet": clean_snippet,
                    })
                return results
    except Exception as e:  # noqa: BLE001
        logger.debug("Fallback de Wikipedia no disponible: %s", e)
    return []


def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Busca información actualizada en internet en tiempo real sobre cualquier tema, noticia o documentación.
    - Qué es: Herramienta de búsqueda web autónoma para Halibel.
    - Para qué se usa: Consultar hechos recientes, verificar datos técnicos, buscar soluciones a errores
      y obtener enlaces relevantes cuando la información no esté en la memoria o conocimiento base.
    - Qué conecta: Provee resultados al orquestador en `llm_service`.
    """
    clean_query = query.strip()
    if not clean_query:
        return {"status": "error", "error": "La consulta de búsqueda no puede estar vacía."}

    safe_limit = max(1, min(10, int(max_results)))

    results: list[dict[str, str]] = []

    # 1. Intentar búsqueda web mediante DDGS (duckduckgo-search)
    if DDGS is not None:
        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(clean_query, max_results=safe_limit))
                for r in raw_results:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
            logger.info("web_search vía DDGS exitoso para '%s': %d resultados", clean_query, len(results))
        except Exception as e:  # noqa: BLE001
            logger.warning("Búsqueda primaria DDGS falló para '%s' (%s). Probando fallback...", clean_query, e)

    # 2. Si DDGS no arrojó resultados o falló, utilizar respaldo de Wikipedia
    if not results:
        results = _search_wikipedia_fallback(clean_query, max_results=safe_limit)
        if results:
            logger.info("web_search vía Wikipedia Fallback exitoso para '%s': %d resultados", clean_query, len(results))

    if not results:
        return {
            "status": "no_results",
            "query": clean_query,
            "message": f"No se encontraron resultados web para '{clean_query}'.",
            "results": [],
        }

    return {
        "status": "success",
        "query": clean_query,
        "count": len(results),
        "results": results,
    }

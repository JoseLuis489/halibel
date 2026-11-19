from unittest.mock import MagicMock, patch

from app.tools.web import (
    _clean_html_to_text,
    _validate_safe_url,
    fetch_web_page,
    web_search,
)


def test_validate_safe_url_blocks_ssrf():
    """Verifica que URLs peligrosas de loopback, red local o esquemas inválidos sean bloqueadas."""
    # 1. Esquemas no permitidos
    is_safe, err = _validate_safe_url("file:///etc/passwd")
    assert not is_safe
    assert "Solo se admiten" in err

    is_safe, err = _validate_safe_url("ftp://example.com/file")
    assert not is_safe

    # 2. Localhost y loopback directo
    for host in ["http://localhost:8000/api", "http://127.0.0.1:8000", "http://0.0.0.0:3000"]:
        is_safe, err = _validate_safe_url(host)
        assert not is_safe
        assert "denegado por seguridad" in err

    # 3. Rangos de red privada interna
    for ip_url in ["http://192.168.1.1/router", "http://10.0.0.5/secret", "http://172.16.0.1/admin"]:
        is_safe, err = _validate_safe_url(ip_url)
        assert not is_safe
        assert "red interna" in err

    # 4. URLs públicas válidas
    is_safe, err = _validate_safe_url("https://docs.python.org/3/")
    assert is_safe
    assert err == ""


def test_clean_html_to_text():
    """Verifica que el limpiador de HTML remueva scripts, estilos y convierta estructura a texto."""
    raw_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Documentación Oficial</title>
        <style>body { background: black; }</style>
        <script>alert('pwned');</script>
    </head>
    <body>
        <nav><a href="/home">Inicio</a></nav>
        <header><h1>Cabecera del sitio</h1></header>
        <main>
            <h2>Capítulo 1: Introducción</h2>
            <p>Este es el <strong>primer</strong> párrafo con entidades &amp; s&iacute;mbolos.</p>
            <div>Segundo bloque de texto explicativo.</div>
        </main>
        <footer>Pie de página &copy; 2026</footer>
    </body>
    </html>
    """
    title, text = _clean_html_to_text(raw_html)

    assert title == "Documentación Oficial"
    assert "alert" not in text
    assert "background: black" not in text
    assert "Pie de página" not in text
    assert "Capítulo 1: Introducción" in text
    assert "primer" in text
    assert "entidades & símbolos." in text


def test_fetch_web_page_ssrf_blocked():
    """fetch_web_page debe abortar antes de contactar IPs locales o privadas."""
    res = fetch_web_page("http://127.0.0.1:8000/api/v1/memories")
    assert res["status"] == "error"
    assert "denegado" in res["error"]


def test_fetch_web_page_mocked():
    """Simula una respuesta HTTP exitosa y verifica extracción de contenido."""
    mock_html = "<html><head><title>Test Page</title></head><body><h1>Hola</h1><p>Contenido web de prueba.</p></body></html>"

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = mock_html.encode("utf-8")
        mock_resp.text = mock_html
        mock_resp.headers = {"content-type": "text/html"}
        mock_get.return_value = mock_resp

        res = fetch_web_page("https://example.com/test", max_chars=500)
        assert res["status"] == "success"
        assert res["title"] == "Test Page"
        assert "Contenido web de prueba." in res["content"]
        assert not res["truncated"]


def test_fetch_web_page_truncation():
    """Verifica que páginas extensas sean truncadas al límite max_chars especificado."""
    long_html = f"<html><body><p>{'A' * 2000}</p></body></html>"

    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = long_html.encode("utf-8")
        mock_resp.text = long_html
        mock_resp.headers = {"content-type": "text/html"}
        mock_get.return_value = mock_resp

        res = fetch_web_page("https://example.com/long", max_chars=600)
        assert res["status"] == "success"
        assert res["truncated"] is True
        assert len(res["content"]) < 800
        assert "...[Contenido truncado" in res["content"]


def test_web_search_empty_query():
    """Búsqueda con consulta vacía debe retornar error amigable."""
    res = web_search("   ")
    assert res["status"] == "error"
    assert "vacía" in res["error"]


def test_web_search_with_mocked_results():
    """Verifica la serialización estructurada de resultados de búsqueda."""
    mock_results = [
        {"title": "FastAPI Web", "href": "https://fastapi.tiangolo.com", "body": "Framework moderno de Python."},
        {"title": "Python Docs", "href": "https://python.org", "body": "Lenguaje de programación oficial."},
    ]

    with patch("app.tools.web.DDGS") as mock_ddgs_cls:
        instance = MagicMock()
        instance.text.return_value = mock_results
        instance.__enter__.return_value = instance
        mock_ddgs_cls.return_value = instance

        res = web_search("python web framework", max_results=2)
        assert res["status"] == "success"
        assert res["count"] == 2
        assert res["results"][0]["title"] == "FastAPI Web"
        assert res["results"][0]["url"] == "https://fastapi.tiangolo.com"
        assert res["results"][0]["snippet"] == "Framework moderno de Python."

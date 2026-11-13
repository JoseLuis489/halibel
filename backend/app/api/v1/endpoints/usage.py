from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.services.usage import usage_service

router = APIRouter()

@router.get("/api/v1/usage/stats")
async def get_usage_stats():
    """
    Retorna las métricas agregadas de consumo en formato JSON.
    - Qué es: Endpoint de API (/api/v1/usage/stats).
    - Para qué se usa: Permitir a clientes consultar estadísticas programáticamente.
    - Qué conecta: Llama a `usage_service.get_stats()`.
    """
    return usage_service.get_stats()

@router.get("/admin/usage", response_class=HTMLResponse)
async def usage_dashboard():
    """
    Panel web visual privado 'Halibel — AI Usage Monitor'.
    - Qué es: Vista HTML/CSS renderizada por el servidor (/admin/usage).
    - Para qué se usa: Proporcionar al usuario un dashboard visual e interactivo para auditar
      tokens de entrada, salida, total de peticiones y costo acumulado sin salir del entorno local.
    - Qué conecta: Lee los datos de `usage_service.get_stats()` y genera el HTML dinámicamente.
    """
    stats = usage_service.get_stats()
    
    # Filas de la tabla de registros recientes
    table_rows = ""
    for log in stats["recent_logs"]:
        table_rows += f"""
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #94a3b8; font-size: 13px;">{log['timestamp']}</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #38bdf8; font-size: 13px; font-family: monospace;">{log['model']}</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #cbd5e1; font-size: 13px; text-align: right;">{log['prompt_tokens']:,}</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #cbd5e1; font-size: 13px; text-align: right;">{log['candidates_tokens']:,}</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #e2e8f0; font-size: 13px; text-align: right; font-weight: 600;">{log['total_tokens']:,}</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #1e293b; color: #34d399; font-size: 13px; text-align: right; font-family: monospace;">${log['cost_usd']:.5f}</td>
        </tr>
        """
    
    if not stats["recent_logs"]:
        table_rows = """
        <tr>
            <td colspan="6" style="padding: 24px; text-align: center; color: #64748b; font-size: 14px;">
                Aún no hay llamadas registradas. Envía un mensaje a Halibel para ver tus métricas aquí.
            </td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Halibel — AI Usage Monitor</title>
        <style>
            * {{
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            }}
            body {{
                background-color: #07090e;
                color: #f1f5f9;
                min-height: 100vh;
                padding: 40px 20px;
                display: flex;
                justify-content: center;
            }}
            .container {{
                width: 100%;
                max-width: 860px;
            }}
            .card-panel {{
                background: #0d121f;
                border: 1px solid #1e293b;
                border-radius: 20px;
                padding: 32px;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
            }}
            .header-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 28px;
            }}
            .title {{
                font-size: 22px;
                font-weight: 700;
                letter-spacing: -0.3px;
                color: #ffffff;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .btn-refresh {{
                background: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                padding: 8px 16px;
                border-radius: 10px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
                transition: all 0.2s;
            }}
            .btn-refresh:hover {{
                background: #334155;
                color: #ffffff;
            }}
            .metrics-grid {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 24px;
                margin-bottom: 28px;
            }}
            .metric-box {{
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 14px;
                padding: 20px;
            }}
            .metric-label {{
                font-size: 13px;
                color: #94a3b8;
                margin-bottom: 8px;
                font-weight: 500;
            }}
            .metric-value {{
                font-size: 32px;
                font-weight: 800;
                letter-spacing: -0.5px;
                color: #ffffff;
            }}
            .metric-value.cost {{
                color: #34d399;
            }}
            .footer-note {{
                font-size: 12px;
                color: #64748b;
                line-height: 1.5;
                margin-top: 10px;
            }}
            .history-title {{
                font-size: 15px;
                font-weight: 600;
                color: #cbd5e1;
                margin: 32px 0 16px 0;
            }}
            .table-container {{
                overflow-x: auto;
                border: 1px solid #1e293b;
                border-radius: 12px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                text-align: left;
            }}
            th {{
                background: #111827;
                padding: 12px 14px;
                font-size: 12px;
                color: #94a3b8;
                font-weight: 600;
                border-bottom: 1px solid #1e293b;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card-panel">
                <div class="header-row">
                    <div class="title">
                        <span>✨ Halibel — AI Usage Monitor</span>
                    </div>
                    <button class="btn-refresh" onclick="window.location.reload()">🔄 Actualizar</button>
                </div>

                <div class="metrics-grid">
                    <div class="metric-box">
                        <div class="metric-label">Tokens de entrada</div>
                        <div class="metric-value">{stats['total_prompt_tokens']:,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Tokens de salida</div>
                        <div class="metric-value">{stats['total_candidates_tokens']:,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Solicitudes</div>
                        <div class="metric-value">{stats['total_requests']:,}</div>
                    </div>
                    <div class="metric-box">
                        <div class="metric-label">Costo acumulado</div>
                        <div class="metric-value cost">${stats['total_cost_usd']:.4f}</div>
                    </div>
                </div>

                <div class="footer-note">
                    Métricas almacenadas localmente en <code>backend/data/halibel.db</code>.<br>
                    Modelo activo actualmente: <strong>{stats['current_model']}</strong>.
                </div>

                <div class="history-title">Últimas llamadas registradas</div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Fecha y Hora</th>
                                <th>Modelo</th>
                                <th style="text-align: right;">Entrada</th>
                                <th style="text-align: right;">Salida</th>
                                <th style="text-align: right;">Total</th>
                                <th style="text-align: right;">Costo Est.</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
DESKTOP_DIR="$PROJECT_ROOT/desktop"

echo "=================================================="
echo "           🚀 Iniciando Halibel AI Companion       "
echo "=================================================="

# Verificar archivo .env en backend
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo "⚠️  No se encontró backend/.env. Creando a partir de .env.example..."
    cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
    echo "💡 Recuerda configurar tu GEMINI_API_KEY en backend/.env"
fi

# Detectar python (pyenv o virtualenv)
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "❌ No se encontró Python."
    exit 1
fi

echo "🔹 [1/2] Iniciando Backend FastAPI en http://127.0.0.1:8000..."
cd "$BACKEND_DIR"
PYTHONPATH="$BACKEND_DIR" "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

# Limpieza al cerrar con Ctrl+C
cleanup() {
    echo ""
    echo "🛑 Deteniendo Halibel..."
    kill $BACKEND_PID 2>/dev/null || true
    wait $BACKEND_PID 2>/dev/null || true
    echo "✅ Halibel detenido."
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Esperar a que el backend esté listo
echo "⏳ Esperando a que el backend responda..."
for i in {1..15}; do
    if curl -s http://127.0.0.1:8000/api/v1/health | grep -q "online"; then
        echo "✅ Backend en línea!"
        break
    fi
    sleep 0.5
done

echo "🔹 [2/2] Iniciando Desktop Client (Electron + React)..."
cd "$DESKTOP_DIR"
pnpm run dev

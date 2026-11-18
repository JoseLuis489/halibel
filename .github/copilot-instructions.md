# Instrucciones del repositorio — Halibel

## Visión general y arquitectura
Halibel es un asistente de escritorio personal compuesto por dos capas:
- **Backend (`backend/`)**: Python 3.11+, FastAPI, Pydantic v2, `google-genai`, WebSockets (`/api/v1/ws/chat`) y SQLite (`sqlite3` estándar con modo WAL).
- **Desktop (`desktop/`)**: Electron 33, React 18, TypeScript y Vite (`pnpm`). Electron ejecuta el backend empaquetado con PyInstaller como sidecar en producción.

## Reglas de seguridad obligatorias
- **Sistema de archivos (`backend/app/tools/filesystem.py`)**: Toda operación de disco debe validar primero la ruta resuelta (`Path.resolve()`) con `_check_security_policy()` para impedir acceso a `.env`, `.git`, `.ssh`, llaves criptográficas o `halibel.db*`.
- **Borrado reversible**: Nunca usar `os.remove`, `unlink` ni `shutil.rmtree`. Toda eliminación de archivos debe usar `send2trash`.
- **Human-in-the-Loop (HITL)**: Cualquier nueva herramienta que modifique o elimine recursos debe registrarse en `DESTRUCTIVE_TOOLS` dentro de `backend/app/tools/registry.py` para exigir aprobación vía WebSocket antes de ejecutarse.
- **Electron (`desktop/electron/`)**: Mantener `contextIsolation: true` y `nodeIntegration: false`. Exponer solo APIs mínimas en `preload.ts`.

## Convenciones de código
- Idioma: Escribir comentarios, docstrings y textos orientados al usuario en **español**.
- Backend: Usar tipado estático moderno (`str | None`, `dict[str, Any]`) y no introducir ORMs pesados sin necesidad; mantener `sqlite3` con `closing()` y manejo de errores resiliente.
- Frontend: Evitar `dangerouslySetInnerHTML`. Respetar el patrón de componentes funcionales y hooks de React.

## Validación antes de abrir un Pull Request
- **Backend**: Ejecutar `cd backend && pytest` y asegurar que todos los tests pasen.
- **Desktop**: Ejecutar `cd desktop && npx tsc --noEmit` para validar que no haya errores de TypeScript.

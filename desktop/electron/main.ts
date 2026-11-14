import { app, BrowserWindow, ipcMain, screen } from 'electron'
import path from 'node:path'
import { spawn, execSync, ChildProcess } from 'node:child_process'
import fs from 'node:fs'

const DIST = path.join(__dirname, '../dist')
process.env.DIST = DIST
process.env.VITE_PUBLIC = app.isPackaged ? DIST : path.join(DIST, '../public')

let win: BrowserWindow | null = null
let backendProcess: ChildProcess | null = null

const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']

// Margen en píxeles respecto a los bordes inferior y derecho del área de trabajo
const SCREEN_MARGIN_X = 24
const SCREEN_MARGIN_Y = 24

// =====================================================================
// 1. BLOQUEO DE INSTANCIA ÚNICA (SINGLE INSTANCE LOCK)
// =====================================================================

const gotTheLock = app.requestSingleInstanceLock()

if (!gotTheLock) {
  // Ya existe una instancia de Halibel ejecutándose: cerramos esta inmediatamente
  // antes de levantar ventanas o intentar ocupar el puerto 8000 del backend.
  app.quit()
} else {
  // Si el usuario intenta abrir una segunda instancia, restauramos y enfocamos la ventana existente
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) {
        win.restore()
      }
      if (!win.isVisible()) {
        win.show()
      }
      win.focus()
    }
  })
}

// =====================================================================
// 2. CÁLCULO GEOMÉTRICO DE ANCLAJE INFERIOR DERECHO
// =====================================================================

/**
 * Calcula el rectángulo exacto (`Rectangle`) anclado a la esquina inferior derecha
 * del área útil del monitor actual (respetando la barra de tareas y multi-monitor).
 */
function getBottomRightBounds(targetWidth: number, targetHeight: number): Electron.Rectangle {
  // Si la ventana ya existe, detectamos en qué monitor está ubicada actualmente;
  // si aún no existe, usamos el monitor principal.
  const targetDisplay = win
    ? screen.getDisplayMatching(win.getBounds())
    : screen.getPrimaryDisplay()

  const { x: areaX, y: areaY, width: areaWidth, height: areaHeight } = targetDisplay.workArea

  return {
    width: Math.round(targetWidth),
    height: Math.round(targetHeight),
    x: Math.round(areaX + areaWidth - targetWidth - SCREEN_MARGIN_X),
    y: Math.round(areaY + areaHeight - targetHeight - SCREEN_MARGIN_Y),
  }
}

// =====================================================================
// 3. GESTIÓN DEL PROCESO SIDECAR (BACKEND FASTAPI)
// =====================================================================

/**
 * Inicia el proceso en segundo plano de Python (FastAPI) cuando la app está empaquetada.
 */
function startBackendSidecar() {
  if (!app.isPackaged) return

  const isWin = process.platform === 'win32'
  const exeName = isWin ? 'halibel-backend.exe' : 'halibel-backend'
  const backendPath = path.join(process.resourcesPath, 'bin', exeName)

  if (fs.existsSync(backendPath)) {
    console.log('Iniciando runtime de Halibel (Sidecar):', backendPath)
    try {
      backendProcess = spawn(backendPath, [], {
        detached: false,
        stdio: 'ignore',
        windowsHide: true, // Evita que parpadee una ventana negra de consola en Windows
      })
      backendProcess.on('error', (err) => {
        console.error('Error al ejecutar el backend sidecar:', err)
      })
    } catch (err) {
      console.error('Excepción al levantar el backend sidecar:', err)
    }
  } else {
    console.warn('No se encontró el ejecutable del backend en:', backendPath)
  }
}

/**
 * Detiene de forma segura el backend y cualquier subproceso hijo (ej. PyInstaller).
 */
function stopBackendSidecar() {
  if (!backendProcess || backendProcess.killed) return

  console.log('Cerrando backend sidecar...')
  try {
    if (process.platform === 'win32' && backendProcess.pid) {
      // /T termina el árbol completo de subprocesos; /F fuerza el cierre limpio
      execSync(`taskkill /pid ${backendProcess.pid} /T /F`, { stdio: 'ignore' })
    } else {
      backendProcess.kill('SIGTERM')
    }
  } catch {
    // El proceso ya había finalizado
  } finally {
    backendProcess = null
  }
}

// =====================================================================
// 4. CREACIÓN DE VENTANA Y CANALES IPC
// =====================================================================

function createWindow() {
  const initialBounds = getBottomRightBounds(360, 520)

  win = new BrowserWindow({
    width: initialBounds.width,
    height: initialBounds.height,
    x: initialBounds.x,
    y: initialBounds.y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    hasShadow: false,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // Mantiene la ventana flotante por encima de otras aplicaciones
  win.setAlwaysOnTop(true, 'screen-saver')

  win.webContents.on('did-finish-load', () => {
    win?.webContents.send('main-process-message', new Date().toLocaleString())
  })

  win.on('closed', () => {
    win = null
  })

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL)
  } else {
    win.loadFile(path.join(DIST, 'index.html'))
  }
}

// Minimiza la ventana desde el botón de la cabecera en React
ipcMain.handle('window:minimize', () => {
  win?.minimize()
})

// Cierra completamente la aplicación desde la interfaz
ipcMain.handle('window:close', () => {
  app.quit()
})

// Redimensiona dinámicamente la ventana manteniendo fija la esquina inferior derecha
ipcMain.handle('window:resize', (_event, width: number, height: number) => {
  if (!win || win.isDestroyed()) return

  const newBounds = getBottomRightBounds(width, height)
  // setBounds actualiza posición (x, y) y tamaño (width, height) de forma atómica sin parpadeos
  win.setBounds(newBounds, false)
})

// =====================================================================
// 5. CICLO DE VIDA DE ELECTRON
// =====================================================================

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  stopBackendSidecar()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && gotTheLock) {
    createWindow()
  }
})

// Solo inicializamos el backend y la ventana si esta es la instancia principal autorizada
if (gotTheLock) {
  app.whenReady().then(() => {
    startBackendSidecar()
    createWindow()
  })
}
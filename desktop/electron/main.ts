import { app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage, desktopCapturer } from 'electron'
import path from 'node:path'
import { spawn, execSync, ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import crypto from 'node:crypto'

// Secreto criptográfico efímero para autenticar el IPC local entre Electron y FastAPI
const IPC_SECRET = process.env.HALIBEL_IPC_SECRET || crypto.randomUUID()
process.env.HALIBEL_IPC_SECRET = IPC_SECRET

const DIST = path.join(__dirname, '../dist')
process.env.DIST = DIST
process.env.VITE_PUBLIC = app.isPackaged ? DIST : path.join(DIST, '../public')

let win: BrowserWindow | null = null
let tray: Tray | null = null
let backendProcess: ChildProcess | null = null
let isQuitting = false

const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']

// Margen en píxeles respecto a los bordes inferior y derecho del área de trabajo
const SCREEN_MARGIN_X = 24
const SCREEN_MARGIN_Y = 24

// =====================================================================
// 0. SILENCIADO DE LOGS INTERNOS DE CHROMIUM / GPU
// =====================================================================
// Debe ejecutarse ANTES de `app.whenReady()` para filtrar avisos ruidosos
// de compositor transparente y GPU (gl_surface_presentation_helper, atom_cache, etc.)
app.commandLine.appendSwitch('log-level', '3')
app.commandLine.appendSwitch('disable-logging')

// =====================================================================
// 1. BLOQUEO DE INSTANCIA ÚNICA (SINGLE INSTANCE LOCK)
// =====================================================================

const gotTheLock = app.requestSingleInstanceLock()

if (!gotTheLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    showAndFocusWindow()
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

/**
 * Restaura, muestra y enfoca la ventana de Halibel de forma segura en cualquier SO.
 */
function showAndFocusWindow() {
  if (!win) {
    createWindow()
    return
  }
  if (win.isMinimized()) {
    win.restore()
  }
  if (!win.isVisible()) {
    win.show()
  }
  win.focus()
}

// =====================================================================
// 3. BANDEJA DEL SISTEMA (SYSTEM TRAY)
// =====================================================================

/**
 * Genera un icono circular RGBA en memoria (16x16) como respaldo garantizado
 * en caso de que aún no exista un archivo `icon.png` / `icon.ico` en `VITE_PUBLIC`.
 */
function createFallbackTrayIcon(): Electron.NativeImage {
  const size = 16
  const buffer = Buffer.alloc(size * size * 4)
  const center = (size - 1) / 2
  const radius = 6.5

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dist = Math.hypot(x - center, y - center)
      const offset = (y * size + x) * 4

      if (dist <= radius) {
        // Gradiente sutil cian-índigo acorde a la estética de la esfera de Halibel
        buffer[offset] = 56      // R
        buffer[offset + 1] = 189 // G
        buffer[offset + 2] = 248 // B
        buffer[offset + 3] = 255 // A
      } else {
        buffer[offset + 3] = 0   // Transparente fuera del círculo
      }
    }
  }

  return nativeImage.createFromBuffer(buffer, { width: size, height: size })
}

/**
 * Crea y configura el icono residente en la bandeja del sistema (Windows / Linux / macOS).
 */
function createSystemTray() {
  if (tray) return

  const publicDir = process.env.VITE_PUBLIC || ''
  const iconCandidates = [
    path.join(publicDir, process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    path.join(publicDir, 'icon.png'),
  ]

  const existingIconPath = iconCandidates.find((p) => fs.existsSync(p))
  const trayIcon = existingIconPath
    ? nativeImage.createFromPath(existingIconPath).resize({ width: 16, height: 16 })
    : createFallbackTrayIcon()

  tray = new Tray(trayIcon)
  tray.setToolTip('Halibel AI Companion')

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Mostrar / Enfocar Halibel',
      click: () => showAndFocusWindow(),
    },
    {
      label: 'Ocultar en bandeja',
      click: () => win?.hide(),
    },
    {
      label: 'Reanclar en esquina inferior derecha',
      click: () => {
        if (!win || win.isDestroyed()) return
        const { width, height } = win.getBounds()
        win.setBounds(getBottomRightBounds(width, height), false)
        showAndFocusWindow()
      },
    },
    { type: 'separator' },
    {
      label: 'Reiniciar aplicación',
      click: () => {
        isQuitting = true
        stopBackendSidecar()
        app.relaunch()
        app.exit(0)
      },
    },
    {
      label: 'Salir de Halibel',
      click: () => {
        isQuitting = true
        app.quit()
      },
    },
  ])

  tray.setContextMenu(contextMenu)

  // Clic izquierdo directo en el icono de la bandeja alterna visibilidad
  tray.on('click', () => {
    if (!win) {
      createWindow()
      return
    }
    if (win.isVisible() && !win.isMinimized()) {
      win.hide()
    } else {
      showAndFocusWindow()
    }
  })
}

// =====================================================================
// 4. GESTIÓN DEL PROCESO SIDECAR (BACKEND FASTAPI)
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
        windowsHide: true,
        env: {
          ...process.env,
          ...(process.env.HALIBEL_IPC_SECRET
            ? { HALIBEL_IPC_SECRET: process.env.HALIBEL_IPC_SECRET }
            : {}),
        },
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
// 5. CREACIÓN DE VENTANA Y CANALES IPC
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

  // En Linux/Windows, si se cierra la ventana sin pedir salida explícita, ocultamos al Tray
  win.on('close', (event) => {
    if (!isQuitting && tray) {
      event.preventDefault()
      win?.hide()
    }
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

// Minimiza / oculta la ventana hacia la bandeja del sistema de forma segura en Windows y Linux
ipcMain.handle('window:minimize', () => {
  if (!win) return
  if (tray) {
    win.hide()
  } else {
    win.minimize()
  }
})

// Cierra completamente la aplicación desde la interfaz
ipcMain.handle('window:close', () => {
  isQuitting = true
  app.quit()
})

// Redimensiona dinámicamente la ventana manteniendo fija la esquina inferior derecha
ipcMain.handle('window:resize', (_event, width: number, height: number) => {
  if (!win || win.isDestroyed()) return

  // Sanitización y límites defensivos para evitar desbordamientos o tamaños inválidos
  const safeWidth = Math.max(80, Math.min(1920, Math.round(Number(width)) || 360))
  const safeHeight = Math.max(80, Math.min(2160, Math.round(Number(height)) || 520))

  const newBounds = getBottomRightBounds(safeWidth, safeHeight)
  win.setBounds(newBounds, false)
})

// Retorna el secreto efímero generado para autenticación del WebSocket e IPC local
ipcMain.handle('auth:get-token', () => {
  return IPC_SECRET
})

// Captura la pantalla primaria en resolución optimizada (1280x720 JPEG) para percepción visual del agente
ipcMain.handle('screen:capture', async () => {
  try {
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: 1280, height: 720 },
    })
    if (sources && sources.length > 0) {
      const primarySource = sources[0]
      const jpegBuffer = primarySource.thumbnail.toJPEG(80)
      return `data:image/jpeg;base64,${jpegBuffer.toString('base64')}`
    }
    return null
  } catch (err) {
    console.error('Error al capturar pantalla con desktopCapturer:', err)
    return null
  }
})

// =====================================================================
// 6. CICLO DE VIDA DE ELECTRON
// =====================================================================

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && isQuitting) {
    app.quit()
  }
})

app.on('before-quit', () => {
  isQuitting = true
  stopBackendSidecar()
  if (tray) {
    tray.destroy()
    tray = null
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && gotTheLock) {
    createWindow()
  } else {
    showAndFocusWindow()
  }
})

if (gotTheLock) {
  app.whenReady().then(() => {
    startBackendSidecar()
    createSystemTray()
    createWindow()
  })
}
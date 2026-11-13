import { app, BrowserWindow, ipcMain, screen } from 'electron'
import path from 'node:path'
import { spawn, ChildProcess } from 'node:child_process'
import fs from 'node:fs'

const DIST = path.join(__dirname, '../dist')
process.env.DIST = DIST
process.env.VITE_PUBLIC = app.isPackaged ? DIST : path.join(DIST, '../public')

let win: BrowserWindow | null
let backendProcess: ChildProcess | null = null

const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']

function startBackendSidecar() {
  if (app.isPackaged) {
    const isWin = process.platform === 'win32'
    const exeName = isWin ? 'halibel-backend.exe' : 'halibel-backend'
    const backendPath = path.join(process.resourcesPath, 'bin', exeName)

    if (fs.existsSync(backendPath)) {
      console.log('Iniciando runtime de Halibel (Sidecar):', backendPath)
      try {
        backendProcess = spawn(backendPath, [], {
          detached: false,
          stdio: 'ignore',
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
}

function createWindow() {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { width, height } = primaryDisplay.workAreaSize

  win = new BrowserWindow({
    width: 360,
    height: 520,
    x: width - 390,
    y: height - 550,
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

  // Permite arrastrar la ventana manteniendo el fondo transparente
  win.setAlwaysOnTop(true, 'screen-saver')

  win.webContents.on('did-finish-load', () => {
    win?.webContents.send('main-process-message', new Date().toLocaleString())
  })

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL)
  } else {
    win.loadFile(path.join(DIST, 'index.html'))
  }
}

// Handlers de IPC
ipcMain.handle('window:minimize', () => {
  win?.minimize()
})

ipcMain.handle('window:close', () => {
  app.quit()
})

ipcMain.handle('window:resize', (_event, width: number, height: number) => {
  if (win) {
    win.setSize(width, height)
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  if (backendProcess && !backendProcess.killed) {
    console.log('Cerrando backend sidecar...')
    backendProcess.kill()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

app.whenReady().then(() => {
  startBackendSidecar()
  createWindow()
})

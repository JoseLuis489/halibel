import { contextBridge, ipcRenderer } from 'electron'

/**
 * Script de precarga seguro (Preload Script).
 * - Qué es: Puente de seguridad con aislamiento de contexto (`contextIsolation: true`).
 * - Para qué se usa: Exponer únicamente métodos seguros y específicos a la ventana de React (`window.halibelAPI`),
 *   impidiendo que el frontend tenga acceso directo a Node.js o al sistema operativo.
 * - Qué conecta: Conecta las llamadas del componente `App.tsx` con los handlers `ipcMain` en `main.ts`.
 */
contextBridge.exposeInMainWorld('halibelAPI', {
  // Minimiza la ventana del escritorio
  minimize: () => ipcRenderer.invoke('window:minimize'),
  // Cierra la aplicación
  close: () => ipcRenderer.invoke('window:close'),
  // Ajusta las dimensiones de la ventana (ej. 100x100 para solo la esfera, 360x520 con el chat abierto)
  resize: (width: number, height: number) => ipcRenderer.invoke('window:resize', width, height),
})

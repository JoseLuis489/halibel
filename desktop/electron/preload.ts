import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('halibelAPI', {
  minimize: () => ipcRenderer.invoke('window:minimize'),
  close: () => ipcRenderer.invoke('window:close'),
  resize: (width: number, height: number) => ipcRenderer.invoke('window:resize', width, height),
})

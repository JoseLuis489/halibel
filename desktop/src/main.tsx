import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

/**
 * Punto de entrada del frontend de React en el proceso Renderer de Electron.
 * - Qué es: Montaje del árbol de componentes React en el nodo DOM raíz (`#root`).
 * - Para qué se usa: Inicializar la aplicación con StrictMode activado para verificación en desarrollo.
 * - Qué conecta: Renderiza `App.tsx` dentro de `index.html`.
 */
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

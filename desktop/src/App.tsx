import React, { useState, useEffect, useRef } from 'react'
import { Send, X, Minus, Sparkles } from 'lucide-react'

// Estructura de cada mensaje en el historial del chat
interface Message {
  role: 'user' | 'halibel'
  content: string
}

// Estados visuales y anímicos de la esfera compañera
type CompanionState = 'idle' | 'thinking' | 'speaking'

// Declaración global para invocar la API expuesta por preload.ts
declare global {
  interface Window {
    halibelAPI?: {
      minimize: () => Promise<void>
      close: () => Promise<void>
      resize: (width: number, height: number) => Promise<void>
    }
  }
}

/**
 * Componente principal de la interfaz de Halibel.
 * - Qué es: Componente funcional de React que gestiona el estado visual de la esfera y el panel de conversación.
 * - Para qué se usa:
 *     1. Mostrar la esfera interactiva con animaciones de respiración, pensamiento o habla.
 *     2. Renderizar el panel de chat con efecto Glassmorphism.
 *     3. Gestionar la conexión WebSocket en tiempo real con el backend de FastAPI.
 * - Qué conecta: Conecta la interacción del usuario en pantalla con el proceso principal de Electron (redimensionamiento)
 *   y con el servidor local en `ws://127.0.0.1:8000/api/v1/ws/chat`.
 */
export default function App() {
  const [isOpen, setIsOpen] = useState(true)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'halibel',
      content: 'Hola. Estoy aquí. ¿En qué puedo acompañarte u organizarte hoy?'
    }
  ])
  const [state, setState] = useState<CompanionState>('idle')
  const socketRef = useRef<WebSocket | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  /**
   * Auto-desplazamiento suave al último mensaje recibido.
   * - Para qué se usa: Mantener siempre a la vista la última respuesta que genera Halibel.
   */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /**
   * Sincronización del tamaño de la ventana nativa según el estado del panel.
   * - Para qué se usa: Reducir la ventana a 100x100 cuando solo se ve la esfera, o ampliar a 360x520
   *   cuando el chat está desplegado.
   * - Qué conecta: Invoca `window.halibelAPI.resize` expuesto en `preload.ts`.
   */
  useEffect(() => {
    if (window.halibelAPI?.resize) {
      if (isOpen) {
        window.halibelAPI.resize(360, 520)
      } else {
        // Ventana compacta para cuando solo se muestra el orbe flotante
        window.halibelAPI.resize(100, 100)
      }
    }
  }, [isOpen])

  /**
   * Inicialización y gestión de la conexión WebSocket con el backend local.
   * - Para qué se usa:
   *     1. Conectarse a `ws://127.0.0.1:8000/api/v1/ws/chat`.
   *     2. Escuchar los eventos del streaming (`stream_start`, `chunk`, `stream_end`, `error`).
   *     3. Acumular los tokens de forma inmutable para evitar duplicaciones de texto.
   *     4. Reintentar automáticamente la conexión si el backend se reinicia.
   */
  useEffect(() => {
    const connectWS = () => {
      const ws = new WebSocket('ws://127.0.0.1:8000/api/v1/ws/chat')
      socketRef.current = ws

      ws.onopen = () => {
        console.log('Conectado al runtime local de Halibel')
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'stream_start') {
            setState('speaking')
            setMessages((prev) => [...prev, { role: 'halibel', content: '' }])
          } else if (data.type === 'chunk') {
            setState('speaking')
            // Actualización inmutable: clona el último mensaje y concatena el fragmento
            setMessages((prev) => {
              if (prev.length === 0) return prev
              const lastIndex = prev.length - 1
              const last = prev[lastIndex]
              if (!last || last.role !== 'halibel') return prev
              return [
                ...prev.slice(0, lastIndex),
                { ...last, content: last.content + data.content }
              ]
            })
          } else if (data.type === 'stream_end') {
            setState('idle')
          } else if (data.type === 'error') {
            setState('idle')
            setMessages((prev) => [
              ...prev,
              { role: 'halibel', content: `[Error: ${data.error}]` }
            ])
          }
        } catch (err) {
          console.error('Error procesando mensaje WebSocket:', err)
        }
      }

      ws.onclose = () => {
        console.log('Desconectado del runtime. Reintentando en 3s...')
        setTimeout(connectWS, 3000)
      }
    }

    connectWS()

    return () => {
      socketRef.current?.close()
    }
  }, [])

  /**
   * Envía el mensaje del usuario al modelo de IA a través del WebSocket.
   * - Para qué se usa:
   *     1. Añadir el mensaje a la lista local.
   *     2. Cambiar la esfera al estado `thinking` (violeta parpadeante).
   *     3. Enviar el texto y los últimos 8 turnos de historial al backend.
   * - Qué conecta: Serializa el JSON y lo emite a través de `socketRef.current.send`.
   */
  const sendMessage = () => {
    if (!input.trim() || state !== 'idle') return

    const userText = input.trim()
    const newMessages = [...messages, { role: 'user' as const, content: userText }]
    setMessages(newMessages)
    setInput('')
    setState('thinking')

    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({
          message: userText,
          history: messages.slice(-8).map((m) => ({
            role: m.role === 'user' ? 'user' : 'model',
            content: m.content
          }))
        })
      )
    } else {
      // Mensaje de contingencia si el backend aún no está encendido
      setTimeout(() => {
        setMessages((prev) => [
          ...prev,
          {
            role: 'halibel',
            content: 'El runtime local de Halibel (FastAPI en el puerto 8000) no parece estar activo. Asegúrate de iniciar el backend.'
          }
        ])
        setState('idle')
      }, 500)
    }
  }

  /**
   * Capturador de pulsación de teclado en el campo de texto.
   * - Para qué se usa: Permitir enviar el mensaje presionando la tecla Enter.
   */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      sendMessage()
    }
  }

  return (
    <div className="app-container">
      {isOpen && (
        <div className="chat-panel">
          {/* Cabecera arrastrable con acciones de minimizar y cerrar */}
          <div className="panel-header">
            <div className="header-title">
              <Sparkles size={16} />
              <span>Halibel</span>
            </div>
            <div className="header-actions">
              <button
                className="btn-icon"
                onClick={() => window.halibelAPI?.minimize()}
                title="Minimizar"
              >
                <Minus size={14} />
              </button>
              <button
                className="btn-icon"
                onClick={() => setIsOpen(false)}
                title="Colapsar a esfera"
              >
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Área de historial de mensajes con scroll automático */}
          <div className="chat-messages">
            {messages.map((msg, index) => (
              <div key={index} className={`message-bubble ${msg.role}`}>
                {msg.content}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Campo de entrada de texto y botón de envío */}
          <div className="chat-input-area">
            <input
              type="text"
              className="chat-input"
              placeholder="Habla o pide algo a Halibel..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={state !== 'idle'}
              autoFocus
            />
            <button
              className="btn-send"
              onClick={sendMessage}
              disabled={!input.trim() || state !== 'idle'}
            >
              <Send size={15} />
            </button>
          </div>
        </div>
      )}

      {/* Esfera / Orbe interactivo flotante con animación según el estado */}
      <div
        className={`orb-trigger ${state}`}
        onClick={() => setIsOpen(!isOpen)}
        title={isOpen ? "Ocultar panel" : "Hablar con Halibel"}
      >
        <div className="orb-core" />
      </div>
    </div>
  )
}

import React, { useState, useEffect, useRef } from 'react'
import { Send, X, Minus, Sparkles, Square, WifiOff } from 'lucide-react'

// Estructura de cada mensaje en el historial del chat
interface Message {
  role: 'user' | 'halibel'
  content: string
}

// Estados visuales y anímicos de la esfera compañera
type CompanionState = 'idle' | 'thinking' | 'speaking'

// Estados de conexión con el backend local (FastAPI)
type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'

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
 * Renderizador ligero de Markdown sin dependencias externas.
 * - Qué es: Parser seguro basado en nodos React (sin usar dangerouslySetInnerHTML).
 * - Para qué se usa: Formatear bloques de código (```), código inline (`), negritas (**),
 *   viñetas de listas (- / *) y saltos de línea para que las respuestas de Fase 2 sean legibles.
 */
function FormattedMessage({ content }: { content: string }) {
  // 1. Separar bloques de código multilínea (```codigo```) del resto del texto
  const blockParts = content.split(/(```[\s\S]*?```)/g)

  const renderInline = (text: string, keyPrefix: string) => {
    // 2. Separar negritas (**texto**) y código en línea (`codigo`)
    const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
    return tokens.map((token, idx) => {
      if (token.startsWith('**') && token.endsWith('**') && token.length > 4) {
        return <strong key={`${keyPrefix}-${idx}`}>{token.slice(2, -2)}</strong>
      }
      if (token.startsWith('`') && token.endsWith('`') && token.length > 2) {
        return (
          <code
            key={`${keyPrefix}-${idx}`}
            style={{
              background: 'rgba(255, 255, 255, 0.12)',
              padding: '1px 5px',
              borderRadius: '4px',
              fontFamily: 'monospace',
              fontSize: '0.88em',
            }}
          >
            {token.slice(1, -1)}
          </code>
        )
      }
      return <React.Fragment key={`${keyPrefix}-${idx}`}>{token}</React.Fragment>
    })
  }

  return (
    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.45 }}>
      {blockParts.map((part, blockIdx) => {
        if (part.startsWith('```') && part.endsWith('```')) {
          const codeBody = part.slice(3, -3).replace(/^[a-zA-Z]+\n/, '')
          return (
            <pre
              key={`block-${blockIdx}`}
              style={{
                background: 'rgba(0, 0, 0, 0.35)',
                padding: '8px 10px',
                borderRadius: '6px',
                overflowX: 'auto',
                fontFamily: 'monospace',
                fontSize: '0.84em',
                margin: '6px 0',
              }}
            >
              <code>{codeBody}</code>
            </pre>
          )
        }

        // Procesar línea por línea para embellecer viñetas de listas (- o *)
        const lines = part.split('\n')
        return (
          <React.Fragment key={`text-${blockIdx}`}>
            {lines.map((line, lineIdx) => {
              const listMatch = line.match(/^(\s*)[-*]\s+(.*)$/)
              const formattedLine = listMatch ? (
                <span>
                  {listMatch[1]}• {renderInline(listMatch[2], `${blockIdx}-${lineIdx}`)}
                </span>
              ) : (
                renderInline(line, `${blockIdx}-${lineIdx}`)
              )

              return (
                <React.Fragment key={`line-${blockIdx}-${lineIdx}`}>
                  {formattedLine}
                  {lineIdx < lines.length - 1 && '\n'}
                </React.Fragment>
              )
            })}
          </React.Fragment>
        )
      })}
    </div>
  )
}

/**
 * Componente principal de la interfaz de Halibel.
 * - Qué es: Componente funcional de React que gestiona el estado visual de la esfera y el panel de conversación.
 * - Para qué se usa:
 *     1. Mostrar la esfera interactiva con animaciones de respiración, pensamiento, habla o desconexión.
 *     2. Renderizar el panel de chat con soporte de formato Markdown y estado de conexión en vivo.
 *     3. Gestionar una única conexión WebSocket resiliente con backoff exponencial y cancelación.
 * - Qué conecta: Conecta la UI con Electron (`preload.ts`) y el endpoint `ws://127.0.0.1:8000/api/v1/ws/chat`.
 */
export default function App() {
  const [isOpen, setIsOpen] = useState(true)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'halibel',
      content: 'Hola. Estoy aquí. ¿En qué puedo acompañarte u organizarte hoy?',
    },
  ])
  const [state, setState] = useState<CompanionState>('idle')
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting')

  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptsRef = useRef<number>(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  /**
   * Auto-desplazamiento suave al último mensaje recibido.
   */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /**
   * Sincronización del tamaño de la ventana nativa según el estado del panel.
   */
  useEffect(() => {
    if (window.halibelAPI?.resize) {
      if (isOpen) {
        window.halibelAPI.resize(360, 520)
      } else {
        window.halibelAPI.resize(100, 100)
      }
    }
  }, [isOpen])

  /**
   * Gestión estricta del ciclo de vida del WebSocket (anti-zombies y backoff exponencial).
   * - Evita múltiples conexiones simultáneas en recargas rápidas (HMR).
   * - Limpia tanto el socket como los temporizadores pendientes al desmontar.
   */
  useEffect(() => {
    let isMounted = true

    const connectWS = () => {
      if (!isMounted) return

      // Cerrar cualquier socket residual antes de abrir uno nuevo
      if (socketRef.current) {
        socketRef.current.onclose = null
        socketRef.current.close()
      }

      setConnectionStatus('connecting')
      const ws = new WebSocket('ws://127.0.0.1:8000/api/v1/ws/chat')
      socketRef.current = ws

      ws.onopen = () => {
        if (!isMounted) {
          ws.close()
          return
        }
        console.log('Conectado al runtime local de Halibel')
        setConnectionStatus('connected')
        reconnectAttemptsRef.current = 0
      }

      ws.onmessage = (event) => {
        if (!isMounted) return
        try {
          const data = JSON.parse(event.data)

          if (data.type === 'stream_start') {
            setState('speaking')
            setMessages((prev) => [...prev, { role: 'halibel', content: '' }])
          } else if (data.type === 'chunk') {
            setState('speaking')
            setMessages((prev) => {
              if (prev.length === 0) return prev
              const lastIndex = prev.length - 1
              const last = prev[lastIndex]
              if (!last || last.role !== 'halibel') return prev
              return [
                ...prev.slice(0, lastIndex),
                { ...last, content: last.content + data.content },
              ]
            })
          } else if (data.type === 'stream_end') {
            setState('idle')
          } else if (data.type === 'error') {
            setState('idle')
            setMessages((prev) => [
              ...prev,
              { role: 'halibel', content: `[Error: ${data.error}]` },
            ])
          }
        } catch (err) {
          console.error('Error procesando mensaje WebSocket:', err)
        }
      }

      ws.onerror = () => {
        // Silenciamos el error genérico del navegador; `onclose` gestionará el estado y el reintento
      }

      ws.onclose = () => {
        if (!isMounted) return

        setConnectionStatus('disconnected')
        // Garantizar que la UI no quede bloqueada si el servidor cayó a mitad de una respuesta
        setState('idle')
        socketRef.current = null

        // Backoff exponencial suave: 1.5s -> 3s -> 6s -> máx 10s
        const delay = Math.min(1500 * Math.pow(2, reconnectAttemptsRef.current), 10000)
        reconnectAttemptsRef.current += 1

        console.log(`Desconectado del runtime. Reintentando en ${delay / 1000}s...`)
        reconnectTimeoutRef.current = setTimeout(connectWS, delay)
      }
    }

    connectWS()

    // Limpieza estricta al desmontar o recargar por HMR
    return () => {
      isMounted = false
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      if (socketRef.current) {
        socketRef.current.onclose = null // Evita que el cierre manual dispare un reconect
        socketRef.current.close()
        socketRef.current = null
      }
    }
  }, [])

  /**
   * Envía una señal de cancelación al backend para detener el streaming y ahorrar tokens.
   */
  const cancelGeneration = () => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: 'cancel' }))
    }
    setState('idle')
  }

  /**
   * Envía el mensaje del usuario al modelo de IA a través del WebSocket.
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
          type: 'chat',
          message: userText,
          history: messages.slice(-8).map((m) => ({
            role: m.role === 'user' ? 'user' : 'model',
            content: m.content,
          })),
        })
      )
    } else {
      setTimeout(() => {
        setMessages((prev) => [
          ...prev,
          {
            role: 'halibel',
            content:
              'El runtime local de Halibel (FastAPI en el puerto 8000) está desconectado. Esperando reconexión...',
          },
        ])
        setState('idle')
      }, 300)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      sendMessage()
    }
  }

  const isDisconnected = connectionStatus !== 'connected'

  return (
    <div className="app-container">
      {isOpen && (
        <div className="chat-panel">
          {/* Cabecera arrastrable con indicador de estado de conexión */}
          <div className="panel-header">
            <div className="header-title">
              <Sparkles size={16} />
              <span>Halibel</span>
              {isDisconnected && (
                <span
                  title="Sin conexión con el backend local"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                    fontSize: '11px',
                    color: '#f87171',
                    background: 'rgba(248, 113, 113, 0.12)',
                    padding: '2px 6px',
                    borderRadius: '999px',
                    marginLeft: '6px',
                  }}
                >
                  <WifiOff size={11} />
                  {connectionStatus === 'connecting' ? 'Conectando...' : 'Offline'}
                </span>
              )}
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

          {/* Área de historial de mensajes con formato Markdown y scroll automático */}
          <div className="chat-messages">
            {messages.map((msg, index) => (
              <div key={index} className={`message-bubble ${msg.role}`}>
                <FormattedMessage content={msg.content} />
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Campo de entrada de texto y botón de envío / cancelación */}
          <div className="chat-input-area">
            <input
              type="text"
              className="chat-input"
              placeholder={
                isDisconnected
                  ? 'Esperando conexión con el runtime...'
                  : 'Habla o pide algo a Halibel...'
              }
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={state !== 'idle' || isDisconnected}
              autoFocus
            />
            {state !== 'idle' ? (
              <button
                className="btn-send"
                onClick={cancelGeneration}
                title="Detener generación"
                style={{ backgroundColor: 'rgba(239, 68, 68, 0.8)' }}
              >
                <Square size={13} />
              </button>
            ) : (
              <button
                className="btn-send"
                onClick={sendMessage}
                disabled={!input.trim() || isDisconnected}
                title="Enviar mensaje"
              >
                <Send size={15} />
              </button>
            )}
          </div>
        </div>
      )}

      {/* Esfera / Orbe interactivo flotante con feedback visual si está offline */}
      <div
        className={`orb-trigger ${state} ${isDisconnected ? 'disconnected' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        title={
          isDisconnected
            ? 'Halibel (Sin conexión al backend)'
            : isOpen
              ? 'Ocultar panel'
              : 'Hablar con Halibel'
        }
        style={
          isDisconnected
            ? { filter: 'grayscale(100%) opacity(0.65)', transition: 'filter 0.3s ease' }
            : { transition: 'filter 0.3s ease' }
        }
      >
        <div className="orb-core" />
      </div>
    </div>
  )
}
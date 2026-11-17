import React, { useState, useEffect, useRef } from 'react'
import {
  Send,
  X,
  Minus,
  Sparkles,
  Square,
  WifiOff,
  AlertTriangle,
  Check,
  ArrowDown,
  RotateCcw,
  Brain,
} from 'lucide-react'

// Estructura de solicitud de autorización de herramientas (Human-in-the-Loop)
interface ToolApprovalRequest {
  id: string
  tool: string
  args: Record<string, unknown>
  description: string
  status: 'pending' | 'approved' | 'rejected'
}

// Estructura de cada mensaje en el historial del chat
interface Message {
  role: 'user' | 'halibel'
  content: string
  approval?: ToolApprovalRequest
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
      getIpcToken: () => Promise<string>
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
 *     2. Renderizar el panel de chat con formato Markdown, tarjetas Human-in-the-Loop y scroll inteligente.
 *     3. Mostrar notificaciones sutiles cuando Halibel guarda un nuevo recuerdo o preferencia.
 *     4. Gestionar una única conexión WebSocket resiliente con backoff exponencial y cancelación.
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
  const [showScrollBottom, setShowScrollBottom] = useState(false)
  const [memoryToast, setMemoryToast] = useState<string | null>(null)

  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const memoryToastTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptsRef = useRef<number>(0)

  // Referencias para el Auto-Scroll Inteligente
  const chatContainerRef = useRef<HTMLDivElement>(null)
  const isUserNearBottomRef = useRef<boolean>(true)

  /**
   * Dispara una notificación visual sutil y no intrusiva cuando Halibel aprende o actualiza un recuerdo.
   */
  const triggerMemoryNotification = (label = 'Halibel aprendió una nueva preferencia') => {
    setMemoryToast(label)
    if (memoryToastTimeoutRef.current) {
      clearTimeout(memoryToastTimeoutRef.current)
    }
    memoryToastTimeoutRef.current = setTimeout(() => {
      setMemoryToast(null)
      memoryToastTimeoutRef.current = null
    }, 4200)
  }

  /**
   * Detecta si el usuario está cerca del fondo del contenedor o si subió manualmente a leer.
   */
  const handleChatScroll = () => {
    const container = chatContainerRef.current
    if (!container) return

    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight

    const nearBottom = distanceFromBottom <= 60
    isUserNearBottomRef.current = nearBottom
    setShowScrollBottom(!nearBottom)
  }

  /**
   * Desplaza el contenedor hasta el final de forma controlada.
   */
  const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
    const container = chatContainerRef.current
    if (!container) return

    container.scrollTo({
      top: container.scrollHeight,
      behavior,
    })
    isUserNearBottomRef.current = true
    setShowScrollBottom(false)
  }

  /**
   * Auto-desplazamiento inteligente:
   * - Solo acompaña el streaming si el usuario NO ha subido manualmente a leer arriba.
   */
  useEffect(() => {
    if (isUserNearBottomRef.current) {
      const container = chatContainerRef.current
      if (container) {
        container.scrollTop = container.scrollHeight
      }
    }
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
   */
  useEffect(() => {
    let isMounted = true

    const connectWS = async () => {
      if (!isMounted) return

      if (socketRef.current) {
        socketRef.current.onclose = null
        socketRef.current.close()
      }

      setConnectionStatus('connecting')

      let wsUrl = 'ws://127.0.0.1:8000/api/v1/ws/chat'
      if (window.halibelAPI?.getIpcToken) {
        try {
          const token = await window.halibelAPI.getIpcToken()
          if (token) {
            wsUrl += `?token=${encodeURIComponent(token)}`
          }
        } catch (err) {
          console.warn('No se pudo obtener el token IPC de Electron:', err)
        }
      }

      if (!isMounted) return
      const ws = new WebSocket(wsUrl)
      socketRef.current = ws

      ws.onopen = () => {
        if (!isMounted) {
          ws.close()
          return
        }
        console.log('Conectado al runtime local de Halibel')
        setConnectionStatus('connected')
        reconnectAttemptsRef.current = 0

        // Sincronizar historial persistente de la sesión al conectar
        ws.send(JSON.stringify({ type: 'get_history', session_id: 'default_session' }))
      }

      ws.onmessage = (event) => {
        if (!isMounted) return
        try {
          const data = JSON.parse(event.data)

          if (data.type === 'history') {
            if (Array.isArray(data.messages) && data.messages.length > 0) {
              setMessages(
                data.messages.map((m: { role: string; content: string }) => ({
                  role: m.role === 'user' ? 'user' : 'halibel',
                  content: m.content,
                }))
              )
            }
          } else if (data.type === 'history_cleared') {
            setMessages([
              {
                role: 'halibel',
                content: 'Conversación reiniciada. ¿En qué puedo acompañarte u organizarte hoy?',
              },
            ])
          } else if (data.type === 'memory_updated') {
            // Evento directo opcional del backend al actualizar memoria
            triggerMemoryNotification('Halibel aprendió una nueva preferencia')
          } else if (data.type === 'stream_start') {
            setState('speaking')
            setMessages((prev) => [...prev, { role: 'halibel', content: '' }])
          } else if (data.type === 'chunk') {
            setState('speaking')
            const chunkText = String(data.content || '')

            // Detectar invocación de herramientas de memoria emitidas por `llm.py`
            if (chunkText.includes('Actualizando memoria:')) {
              triggerMemoryNotification('Halibel aprendió una nueva preferencia')
            }

            setMessages((prev) => {
              if (prev.length === 0) return prev
              const lastIndex = prev.length - 1
              const last = prev[lastIndex]
              if (!last || last.role !== 'halibel') return prev
              return [
                ...prev.slice(0, lastIndex),
                { ...last, content: last.content + chunkText },
              ]
            })
          } else if (data.type === 'tool_approval_request') {
            isUserNearBottomRef.current = true
            setState('thinking')
            setMessages((prev) => [
              ...prev,
              {
                role: 'halibel',
                content: '',
                approval: {
                  id: data.tool_call_id,
                  tool: data.tool,
                  args: (data.args as Record<string, unknown>) || {},
                  description: data.description,
                  status: 'pending',
                },
              },
            ])
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
        setState('idle')
        socketRef.current = null

        const delay = Math.min(1500 * Math.pow(2, reconnectAttemptsRef.current), 10000)
        reconnectAttemptsRef.current += 1

        console.log(`Desconectado del runtime. Reintentando en ${delay / 1000}s...`)
        reconnectTimeoutRef.current = setTimeout(connectWS, delay)
      }
    }

    connectWS()

    return () => {
      isMounted = false
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      if (memoryToastTimeoutRef.current) {
        clearTimeout(memoryToastTimeoutRef.current)
        memoryToastTimeoutRef.current = null
      }
      if (socketRef.current) {
        socketRef.current.onclose = null
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
   * Responde a una solicitud de autorización de herramienta interactiva (Human-in-the-Loop).
   */
  const handleApproval = (id: string, approved: boolean) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({
          type: 'tool_approval_response',
          tool_call_id: id,
          approved,
        })
      )
    }
    setMessages((prev) =>
      prev.map((msg) =>
        msg.approval && msg.approval.id === id
          ? {
              ...msg,
              approval: {
                ...msg.approval,
                status: approved ? 'approved' : 'rejected',
              },
            }
          : msg
      )
    )
  }

  /**
   * Vacía el historial de la conversación actual tanto en la vista como en SQLite.
   */
  const clearChat = () => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({
          type: 'clear_history',
          session_id: 'default_session',
        })
      )
    } else {
      setMessages([
        {
          role: 'halibel',
          content: 'Conversación reiniciada. ¿En qué puedo acompañarte u organizarte hoy?',
        },
      ])
    }
  }

  /**
   * Envía el mensaje del usuario al modelo de IA a través del WebSocket.
   */
  const sendMessage = () => {
    if (!input.trim() || state !== 'idle') return

    const userText = input.trim()
    isUserNearBottomRef.current = true
    setShowScrollBottom(false)

    const newMessages = [...messages, { role: 'user' as const, content: userText }]
    setMessages(newMessages)
    setInput('')
    setState('thinking')

    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      const validHistory = messages
        .filter((m) => m.content.trim().length > 0)
        .slice(-8)
        .map((m) => ({
          role: m.role === 'user' ? 'user' : 'model',
          content: m.content,
        }))

      socketRef.current.send(
        JSON.stringify({
          type: 'chat',
          message: userText,
          history: validHistory,
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
        <div className="chat-panel" style={{ position: 'relative' }}>
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
                onClick={clearChat}
                title="Reiniciar conversación"
              >
                <RotateCcw size={14} />
              </button>
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

          {/* Notificación sutil en tiempo real cuando Halibel guarda un recuerdo o preferencia */}
          {memoryToast && (
            <div
              role="status"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                padding: '5px 10px',
                margin: '6px 12px 0',
                borderRadius: '8px',
                fontSize: '11.5px',
                color: '#c4b5fd',
                background: 'rgba(139, 92, 246, 0.16)',
                border: '1px solid rgba(139, 92, 246, 0.35)',
                backdropFilter: 'blur(8px)',
                boxShadow: '0 2px 10px rgba(0, 0, 0, 0.25)',
                transition: 'opacity 0.25s ease',
              }}
            >
              <Brain size={13} color="#a78bfa" />
              <span>{memoryToast}</span>
            </div>
          )}

          {/* Área de historial de mensajes con detección inteligente de scroll */}
          <div
            className="chat-messages"
            ref={chatContainerRef}
            onScroll={handleChatScroll}
          >
            {messages.map((msg, index) => (
              <div key={index} className={`message-bubble ${msg.role}`}>
                {msg.approval && (
                  <div className="approval-card">
                    <div className="approval-header">
                      <AlertTriangle size={15} color="#eab308" />
                      <span>Autorización requerida</span>
                    </div>
                    <p className="approval-desc">{msg.approval.description}</p>
                    {Boolean(msg.approval.args?.path) && (
                      <div className="approval-path">
                        <code>{String(msg.approval.args.path)}</code>
                      </div>
                    )}
                    {msg.approval.status === 'pending' ? (
                      <div className="approval-actions">
                        <button
                          className="btn-approve"
                          onClick={() => handleApproval(msg.approval!.id, true)}
                        >
                          <Check size={13} /> Autorizar
                        </button>
                        <button
                          className="btn-reject"
                          onClick={() => handleApproval(msg.approval!.id, false)}
                        >
                          <X size={13} /> Rechazar
                        </button>
                      </div>
                    ) : (
                      <div className={`approval-status ${msg.approval.status}`}>
                        {msg.approval.status === 'approved' ? '✓ Autorizado' : '✕ Cancelado'}
                      </div>
                    )}
                  </div>
                )}
                {msg.content && <FormattedMessage content={msg.content} />}
              </div>
            ))}
          </div>

          {/* Botón flotante para volver abajo cuando el usuario subió a leer */}
          {showScrollBottom && (
            <button
              onClick={() => scrollToBottom('smooth')}
              title="Ir al mensaje más reciente"
              style={{
                position: 'absolute',
                right: '16px',
                bottom: '64px',
                width: '28px',
                height: '28px',
                borderRadius: '50%',
                border: '1px solid rgba(255, 255, 255, 0.2)',
                background: 'rgba(24, 24, 32, 0.9)',
                color: '#fff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.4)',
                zIndex: 10,
              }}
            >
              <ArrowDown size={14} />
            </button>
          )}

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
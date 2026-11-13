import React, { useState, useEffect, useRef } from 'react'
import { Send, X, Minus, Sparkles } from 'lucide-react'

interface Message {
  role: 'user' | 'halibel'
  content: string
}

type CompanionState = 'idle' | 'thinking' | 'speaking'

declare global {
  interface Window {
    halibelAPI?: {
      minimize: () => Promise<void>
      close: () => Promise<void>
      resize: (width: number, height: number) => Promise<void>
    }
  }
}

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

  // Auto-scroll to bottom of messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Adjust window size depending on open/closed state
  useEffect(() => {
    if (window.halibelAPI?.resize) {
      if (isOpen) {
        window.halibelAPI.resize(360, 520)
      } else {
        // When collapsed to only the orb
        window.halibelAPI.resize(100, 100)
      }
    }
  }, [isOpen])

  // Initialize WebSocket connection to FastAPI backend
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
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last && last.role === 'halibel') {
                last.content += data.content
              }
              return updated
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
          console.error('Error parsing WS message:', err)
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
      // Fallback if backend is not running yet
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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      sendMessage()
    }
  }

  return (
    <div className="app-container">
      {isOpen && (
        <div className="chat-panel">
          {/* Header */}
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

          {/* Messages */}
          <div className="chat-messages">
            {messages.map((msg, index) => (
              <div key={index} className={`message-bubble ${msg.role}`}>
                {msg.content}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Box */}
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

      {/* Floating Interactive Orb / Sphere */}
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

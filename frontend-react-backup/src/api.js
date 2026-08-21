/**
 * WebSocket 客户端：Op 上行 / Event 下行（对标 pi-web 多路复用 + 断线重连）
 * - 断线自动重连，从事件 ID 游标增量补发
 * - 多标签页共享同一会话（各自连接，服务端广播）
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const TOKEN = 'demo-token' // 前端演示 token（生产从 /auth/token 获取）

/** 拉取会话列表（对标 pi-web：按工作目录组织）。 */
export async function fetchSessions() {
  const res = await fetch('/sessions', { headers: { 'x-agent-token': TOKEN } })
  if (!res.ok) throw new Error('加载会话列表失败')
  return (await res.json()).sessions || []
}

/** 切换/创建会话：换 JSONL + 重放（事件溯源：状态永不保存，文件即真相）。 */
export async function switchSession(sessionId) {
  const res = await fetch('/sessions/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-agent-token': TOKEN },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!res.ok) throw new Error('切换会话失败')
  return res.json()
}

export function useCockpit({ sessionId = 'web', onEvent } = {}) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState([])
  const wsRef = useRef(null)
  const cursorRef = useRef(0)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    let closed = false
    let retry = 0

    // 会话切换 → cursor 归零（不同会话的游标独立，从该会话开头增量补发）
    cursorRef.current = 0

    function connect() {
      if (closed) return
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${window.location.host}/ws?token=${TOKEN}&cursor=${cursorRef.current}`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        retry = 0
      }
      ws.onmessage = (msg) => {
        try {
          const ev = JSON.parse(msg.data)
          cursorRef.current = Math.max(cursorRef.current, ev.id || 0)
          setEvents((prev) => [...prev, ev])
          onEventRef.current?.(ev)
        } catch {
          /* ignore */
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          retry += 1
          setTimeout(connect, Math.min(1000 * retry, 5000))
        }
      }
      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      closed = true
      wsRef.current?.close()
      setEvents([])
    }
  }, [sessionId])

  /** 发送 Op（意图）到内核 */
  const sendOp = useCallback(
    (op) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
      wsRef.current.send(JSON.stringify(op))
    },
    []
  )

  const sendTurn = useCallback(
    (content, { mode = 'act', files = [], session_id = null } = {}) => {
      sendOp({ type: 'UserTurnStart', content, mode, files, session_id })
    },
    [sendOp]
  )

  const sendApproval = useCallback(
    (approvalId, decision, editedCommand = null) => {
      // 审批二次确认（G5）：ApprovalResponse 需带 confirm=true
      sendOp({
        type: 'ApprovalResponse',
        approval_id: approvalId,
        decision,
        edited_command: editedCommand,
        confirm: true,
      })
    },
    [sendOp]
  )

  const cancelTurn = useCallback((reason = 'user cancelled') => {
    sendOp({ type: 'UserTurnCancel', reason })
  }, [sendOp])

  const compact = useCallback((target = 'context') => {
    sendOp({ type: 'Compact', target })
  }, [sendOp])

  return { connected, events, sendTurn, sendApproval, cancelTurn, compact }
}

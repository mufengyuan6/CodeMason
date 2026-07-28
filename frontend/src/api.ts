/**
 * WebSocket 客户端：Op 上行 / Event 下行（对标 pi-web 多路复用 + 断线重连）
 * - 断线自动重连，从事件 ID 游标增量补发
 * - 多标签页共享同一会话（各自连接，服务端广播）
 */
import { ref, onUnmounted, watch, type Ref } from 'vue'

export interface Event {
  id: number
  type: string
  [key: string]: any
}

export interface Op {
  type: string
  [key: string]: any
}

const TOKEN = 'demo-token' // 前端演示 token（生产从 /auth/token 获取）

/** 拉取会话列表（对标 pi-web：按工作目录组织）。 */
export async function fetchSessions(): Promise<any[]> {
  const res = await fetch('/sessions', { headers: { 'x-agent-token': TOKEN } })
  if (!res.ok) throw new Error('加载会话列表失败')
  return (await res.json()).sessions || []
}

/** 切换/创建会话：换 JSONL + 重放（事件溯源：状态永不保存，文件即真相）。 */
export async function switchSession(sessionId: string): Promise<any> {
  const res = await fetch('/sessions/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-agent-token': TOKEN },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!res.ok) throw new Error('切换会话失败')
  return res.json()
}

/**
 * 驾驶舱 WebSocket 组合式函数
 * - 管理连接状态、事件流、断线重连
 * - 提供发送 Op 的方法
 */
export function useCockpit(options: {
  sessionId?: Ref<string> | string
  onEvent?: (event: Event) => void
} = {}) {
  const { sessionId = 'web', onEvent } = options
  
  const connected = ref(false)
  const events = ref<Event[]>([])
  let ws: WebSocket | null = null
  let cursor = 0
  let closed = false
  let retry = 0
  let onEventCallback = onEvent

  // 监听 sessionId 变化（如果是 ref）
  const sessionIdValue = typeof sessionId === 'string' ? ref(sessionId) : sessionId

  function connect() {
    if (closed) return
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws?token=${TOKEN}&cursor=${cursor}`
    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
      retry = 0
    }
    ws.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as Event
        cursor = Math.max(cursor, ev.id || 0)
        events.value = [...events.value, ev]
        onEventCallback?.(ev)
      } catch {
        /* ignore */
      }
    }
    ws.onclose = () => {
      connected.value = false
      if (!closed) {
        retry += 1
        setTimeout(connect, Math.min(1000 * retry, 5000))
      }
    }
    ws.onerror = () => ws?.close()
  }

  // 启动连接
  connect()

  // 监听 sessionId 变化（会话切换 → cursor 归零）
  if (typeof sessionId !== 'string') {
    watch(sessionIdValue, () => {
      cursor = 0
      events.value = []
      ws?.close()
      connect()
    })
  }

  // 清理
  onUnmounted(() => {
    closed = true
    ws?.close()
    events.value = []
  })

  /** 发送 Op（意图）到内核 */
  const sendOp = (op: Op) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify(op))
  }

  const sendTurn = (content: string, options: { mode?: string; files?: string[]; session_id?: string } = {}) => {
    const { mode = 'act', files = [], session_id = null } = options
    sendOp({ type: 'UserTurnStart', content, mode, files, session_id })
  }

  const sendApproval = (approvalId: string, decision: string, editedCommand?: string) => {
    // 审批二次确认（G5）：ApprovalResponse 需带 confirm=true
    sendOp({
      type: 'ApprovalResponse',
      approval_id: approvalId,
      decision,
      edited_command: editedCommand,
      confirm: true,
    })
  }

  const cancelTurn = (reason = 'user cancelled') => {
    sendOp({ type: 'UserTurnCancel', reason })
  }

  const compact = (target = 'context') => {
    sendOp({ type: 'Compact', target })
  }

  return {
    connected,
    events,
    sendTurn,
    sendApproval,
    cancelTurn,
    compact
  }
}
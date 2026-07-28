<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { Event } from '../api'

interface Turn {
  turnIndex: number
  mode: string
  sessionId: string
  messages: Array<{
    role: 'user' | 'assistant'
    text: string
    complete?: boolean
    id: number
  }>
  items: Event[]
}

interface Props {
  events: Event[]
  sendApproval?: (approvalId: string, decision: string, editedCommand?: string) => void
}

const props = defineProps<Props>()

const scrollRef = ref<HTMLElement | null>(null)
const confirmId = ref<string | null>(null)
const editing = ref<string | null>(null)
const editValue = ref('')
const openDetails = ref<Set<number>>(new Set())

// 自动滚动到底部
watch(() => props.events.length, () => {
  setTimeout(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  }, 100)
})

/**
 * 折叠投影：把同一事件流按"会话轮次"折叠
 * - 对话气泡 minimal：TurnStarted + AgentMessageContentDelta（合并流式文本）
 * - 工具卡片 rich：ExecApprovalRequest / ItemCompleted / Rollback / Error / RootCauseReport
 */
const turns = computed(() => {
  const result: Turn[] = []
  let current: Turn | null = null
  for (const ev of props.events) {
    if (ev.type === 'TurnStarted') {
      current = { turnIndex: ev.turn_index, mode: ev.mode, sessionId: ev.session_id, messages: [], items: [] }
      result.push(current)
    } else if (ev.type === 'AgentMessageContentDelta' || ev.type === 'UserTurnStart') {
      if (!current) {
        current = { turnIndex: 0, mode: 'act', sessionId: '', messages: [], items: [] }
        result.push(current)
      }
      // 用户消息（Op 上行不入 EventLog，这里只兜底）
      if (ev.type === 'UserTurnStart') {
        current.messages.push({ role: 'user', text: ev.content, id: ev.id })
      } else {
        const last = current.messages[current.messages.length - 1]
        if (last && last.role === 'assistant' && last.id === ev.id) {
          last.text += ev.delta || ''
          if (ev.complete) last.complete = true
        } else {
          current.messages.push({ role: 'assistant', text: ev.delta || '', complete: ev.complete, id: ev.id })
        }
      }
    } else {
      if (!current) {
        current = { turnIndex: 0, mode: 'act', sessionId: '', messages: [], items: [] }
        result.push(current)
      }
      current.items.push(ev)
    }
  }
  return result
})

const toggleDetail = (id: number) => {
  const next = new Set(openDetails.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openDetails.value = next
}

const handleApproval = (ev: Event, decision: string) => {
  if (decision === 'approve' && ev.risk_level === 'red') {
    if (confirmId.value !== ev.approval_id) {
      confirmId.value = ev.approval_id
      setTimeout(() => {
        if (confirmId.value === ev.approval_id) {
          confirmId.value = null
        }
      }, 3000)
      return
    }
    confirmId.value = null
  }
  props.sendApproval?.(ev.approval_id, decision)
}

const handleEditSubmit = (ev: Event) => {
  props.sendApproval?.(ev.approval_id, 'edit', editValue.value || ev.command)
  editing.value = null
  editValue.value = ''
}

const startEdit = (ev: Event) => {
  editing.value = ev.approval_id
  editValue.value = ev.command || ''
}

const getBadgeInfo = (ev: Event) => {
  let badge = '系统消息'
  let badgeStyle = {}
  let title = ev.type
  let sub = ''
  
  switch (ev.type) {
    case 'ExecApprovalRequest':
      badge = '审批'
      badgeStyle = { background: 'var(--c-warning)' }
      title = ev.tool_name
      sub = ev.description
      break
    case 'ItemCompleted':
      badge = ev.item_type === 'turn_summary' ? '总结' : '完成'
      badgeStyle = { background: 'var(--c-success)' }
      title = ev.item_type
      sub = typeof ev.content === 'string' ? ev.content.slice(0, 120) : ''
      break
    case 'Rollback':
      badge = '回滚'
      title = `已回滚到 Checkpoint ${ev.checkpoint_id}`
      break
    case 'Error':
      badge = '错误'
      title = ev.message
      break
    case 'RootCauseReport':
      badge = '溯源'
      badgeStyle = { background: 'var(--c-accent, #7c5cff)' }
      title = `溯源报告 ${ev.report_id}`
      sub = `${ev.trigger} · ${ev.status === 'degraded' ? '纯确定性' : '完整链路'} · ${(ev.stages || []).length} 阶段定位`
      break
  }
  
  return { badge, badgeStyle, title, sub }
}
</script>

<template>
  <div ref="scrollRef" class="timeline dialogue-view" data-testid="dialogue-view">
    <div v-if="turns.length === 0" class="timeline-empty">
      <p>对话为空</p>
      <p class="hint">在下方输入任务，对话会显示在这里</p>
    </div>
    
    <div v-for="(turn, ti) in turns" :key="ti" class="dlg-turn" data-testid="dlg-turn">
      <div class="dlg-turn-rail">
        <span class="dlg-turn-num">{{ turn.turnIndex }}</span>
      </div>
      <div class="dlg-turn-body">
        <!-- 消息气泡 -->
        <div
          v-for="(m, mi) in turn.messages"
          :key="`${m.id}-${mi}`"
          :class="['dlg-msg', m.role === 'user' ? 'is-user' : 'is-agent']"
          data-testid="dlg-msg"
        >
          <div :class="['dlg-bubble', m.role === 'user' ? 'is-user' : 'is-agent']">
            {{ m.role === 'user' ? m.text : (m.text || (m.complete ? '' : '思考中…')) }}
            <span v-if="m.role === 'assistant' && !m.complete" class="breath-dots"><i /><i /><i /></span>
          </div>
        </div>
        
        <!-- 工具卡片 -->
        <div v-for="ev in turn.items" :key="ev.id" class="dlg-item-card" data-testid="dlg-item-card">
          <button
            class="dlg-item-head"
            :data-testid="`dlg-item-${ev.id}`"
            @click="toggleDetail(ev.id)"
          >
            <span class="ev-badge" :style="getBadgeInfo(ev).badgeStyle">{{ getBadgeInfo(ev).badge }}</span>
            <span class="dlg-item-title">{{ getBadgeInfo(ev).title }}</span>
            <span v-if="getBadgeInfo(ev).sub" class="dlg-item-sub">{{ getBadgeInfo(ev).sub }}</span>
            <span class="dlg-chevron">{{ openDetails.has(ev.id) ? '▾' : '▸' }}</span>
          </button>
          
          <div v-if="openDetails.has(ev.id)" class="dlg-item-body">
            <!-- ExecApprovalRequest -->
            <template v-if="ev.type === 'ExecApprovalRequest'">
              <code v-if="ev.command" class="ev-command">{{ ev.command }}</code>
              <div v-if="editing === ev.approval_id" class="ev-approval-edit">
                <textarea v-model="editValue" class="appr-edit-input" rows="2" autofocus />
                <button class="btn btn-edit" @click="handleEditSubmit(ev)">提交修改</button>
                <button class="btn btn-ghost" @click="editing = null">取消</button>
              </div>
              <div v-else class="ev-approval-actions">
                <button class="btn btn-approve" data-testid="dlg-approve" @click="handleApproval(ev, 'approve')">
                  {{ confirmId === ev.approval_id ? '再次确认' : '批准' }}
                </button>
                <button class="btn btn-reject" data-testid="dlg-reject" @click="handleApproval(ev, 'reject')">拒绝</button>
                <button class="btn btn-edit" @click="startEdit(ev)">编辑</button>
                <span v-if="ev.risk_level === 'red'" class="risk-tag red">高危</span>
              </div>
            </template>
            
            <!-- ItemCompleted -->
            <template v-else-if="ev.type === 'ItemCompleted'">
              <div v-if="ev.metrics" class="ev-metrics">
                <span v-for="(v, k) in Object.entries(ev.metrics).slice(0, 4)" :key="k" class="metric-chip">{{ v[0] }}: {{ v[1] }}</span>
              </div>
            </template>
            
            <!-- RootCauseReport -->
            <template v-else-if="ev.type === 'RootCauseReport'">
              <div class="dlg-rc-report">
                <div v-if="ev.fix_instructions?.length > 0" class="dlg-rc-section">
                  <b>修复指令</b>
                  <ul>
                    <li v-for="(ins, i) in ev.fix_instructions" :key="i">{{ ins }}</li>
                  </ul>
                </div>
                <div v-if="ev.stages?.length > 0" class="dlg-rc-section">
                  <b>阶段定位</b>
                  <ul>
                    <li v-for="(s, i) in ev.stages" :key="i">[{{ s.stage }}] {{ s.issue }}</li>
                  </ul>
                </div>
                <div v-if="ev.attributions?.length > 0" class="dlg-rc-section">
                  <b>归因假设</b>
                  <ul>
                    <li v-for="(a, i) in ev.attributions" :key="i">
                      {{ a.hypothesis }}
                      <span v-if="a.agent_inferred" class="metric-chip">agent_inferred</span>
                    </li>
                  </ul>
                </div>
              </div>
            </template>
            
            <!-- 其他事件 -->
            <template v-else>
              <pre class="diff-pre diff-pre-inline"><code>{{ JSON.stringify(ev, null, 2).slice(0, 500) }}</code></pre>
            </template>
            
            <!-- 通用理由展示 -->
            <div v-if="ev.rationale" class="dlg-rationale" data-testid="dlg-rationale">
              <span class="metric-chip">理由</span>
              <span class="dlg-rationale-text">{{ ev.rationale }}</span>
              <span class="dlg-rationale-src">（模型自述理由，非验证事实）</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
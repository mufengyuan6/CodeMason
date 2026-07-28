<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import DeliveryBanner from './DeliveryBanner.vue'
import type { Event } from '../api'

interface Props {
  events: Event[]
  sendApproval?: (approvalId: string, decision: string, editedCommand?: string) => void
}

const props = defineProps<Props>()

const scrollRef = ref<HTMLElement | null>(null)
const GUIDE_KEY = 'codemason-guide-dismissed'
const guideDismissed = ref(localStorage.getItem(GUIDE_KEY) === '1')
const confirmId = ref<string | null>(null)
const editing = ref<string | null>(null)
const editValue = ref('')

// 自动滚动到底部
watch(() => props.events.length, () => {
  setTimeout(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight, behavior: 'smooth' })
  }, 100)
})

// 分组合并消息
const groups = computed(() => {
  const messages = new Map()
  const other: Event[] = []
  for (const ev of props.events) {
    if (ev.type === 'AgentMessageContentDelta') {
      const key = ev.message_index ?? ev.id
      const cur = messages.get(key) || { key, session_id: ev.session_id, text: '', complete: false, id: ev.id, ts: ev.ts }
      cur.text += ev.delta || ''
      if (ev.complete) cur.complete = true
      messages.set(key, cur)
    } else {
      other.push(ev)
    }
  }
  return [...messages.values(), ...other].sort((a: any, b: any) => (a.ts ?? 0) - (b.ts ?? 0) || (a.id ?? 0) - (b.id ?? 0))
})

// 最近交付指标
const lastDelivery = computed(() => {
  const done = props.events.filter((e) => e.type === 'ItemCompleted' && e.metrics)
  return done.length > 0 ? done[done.length - 1].metrics : null
})

const handleApproval = (a: Event, decision: string) => {
  if (decision === 'approve' && a.risk_level === 'red') {
    if (confirmId.value !== a.approval_id) {
      confirmId.value = a.approval_id
      setTimeout(() => {
        if (confirmId.value === a.approval_id) {
          confirmId.value = null
        }
      }, 3000)
      return
    }
    confirmId.value = null
  }
  props.sendApproval?.(a.approval_id, decision)
}

const handleEditSubmit = (a: Event) => {
  props.sendApproval?.(a.approval_id, 'edit', editValue.value || a.command)
  editing.value = null
  editValue.value = ''
}

const dismissGuide = () => {
  localStorage.setItem(GUIDE_KEY, '1')
  guideDismissed.value = true
}

const startEdit = (a: Event) => {
  editing.value = a.approval_id
  editValue.value = a.command || ''
}
</script>

<template>
  <div ref="scrollRef" class="timeline" data-testid="timeline">
    <!-- 交付横幅：峰终反馈（始终置顶，可关闭） -->
    <div v-if="lastDelivery" class="timeline-banner-slot">
      <DeliveryBanner :metrics="lastDelivery" @close="() => null" />
    </div>

    <div v-if="groups.length === 0 && !guideDismissed" class="timeline-guide" data-testid="timeline-guide">
      <h3 class="guide-title">三步开始</h3>
      <ol class="guide-list">
        <li><b>发任务</b> —— 在下方输入你想让 Agent 做的事</li>
        <li><b>看每一步</b> —— Agent 的思考与操作实时显示在这里</li>
        <li><b>关键点审批</b> —— 危险操作会在这里等你确认，可批准/拒绝/修改</li>
      </ol>
      <p class="guide-note">一切可取消、可回滚、可审计 —— 改坏了随时回到 Checkpoint</p>
      <button class="btn btn-primary" data-testid="guide-start" @click="dismissGuide">
        开始使用
      </button>
    </div>

    <div v-if="groups.length === 0 && guideDismissed" class="timeline-empty">
      <p>事件流为空</p>
      <p class="hint">在下方输入任务，观察 Agent 的每一步</p>
    </div>

    <div
      v-for="(ev, i) in groups"
      :key="ev.id ?? `${ev.type}-${i}`"
      class="ev-row"
      :style="{ animationDelay: `${Math.min(i, 8) * 40}ms` }"
    >
      <div class="ev-rail" />
      
      <!-- TurnStarted -->
      <div v-if="ev.type === 'TurnStarted'" class="ev-card ev-turn" data-testid="ev-turn">
        <span class="ev-badge">回合</span>
        <div class="ev-body">
          <div class="ev-title">回合 #{{ ev.turn_index }}</div>
          <div class="ev-sub">{{ ev.mode === 'plan' ? '规划模式' : '执行模式' }} · session {{ ev.session_id }}</div>
        </div>
      </div>

      <!-- AgentMessageContentDelta -->
      <div
        v-else-if="ev.type === 'AgentMessageContentDelta'"
        :class="['ev-card', 'ev-msg', ev.complete ? 'is-complete' : 'is-streaming']"
      >
        <span class="ev-badge">Agent</span>
        <div class="ev-body">
          <div class="ev-msg-text">{{ ev.text || (ev.complete ? '' : '思考中…') }}</div>
          <span v-if="!ev.complete" class="breath-dots"><i /><i /><i /></span>
        </div>
      </div>

      <!-- ExecApprovalRequest -->
      <div
        v-else-if="ev.type === 'ExecApprovalRequest'"
        class="ev-card ev-approval is-interactive"
        data-testid="ev-approval"
      >
        <span class="ev-badge" style="background: var(--c-warning)">审批</span>
        <div class="ev-body">
          <div class="ev-title">{{ ev.tool_name }}</div>
          <div class="ev-sub">{{ ev.description }}</div>
          <code v-if="ev.command" class="ev-command">{{ ev.command }}</code>
          <pre v-if="ev.diff_preview" class="diff-pre diff-pre-inline"><code>{{ ev.diff_preview }}</code></pre>
          <div v-if="ev.rationale" class="ev-rationale" data-testid="ev-rationale">
            <span class="metric-chip">理由</span>
            <span class="ev-rationale-text">{{ ev.rationale }}</span>
            <span class="ev-rationale-src">（模型自述理由，非验证事实）</span>
          </div>
          
          <!-- 审批操作 -->
          <div v-if="editing === ev.approval_id" class="ev-approval-edit">
            <textarea v-model="editValue" class="appr-edit-input" rows="2" autofocus />
            <button class="btn btn-edit" @click="handleEditSubmit(ev)">提交修改</button>
            <button class="btn btn-ghost" @click="editing = null">取消</button>
          </div>
          <div v-else class="ev-approval-actions">
            <button
              class="btn btn-approve"
              data-testid="ev-approve"
              @click="handleApproval(ev, 'approve')"
            >
              {{ confirmId === ev.approval_id ? '再次确认' : '批准' }}
            </button>
            <button class="btn btn-reject" data-testid="ev-reject" @click="handleApproval(ev, 'reject')">拒绝</button>
            <button class="btn btn-edit" @click="startEdit(ev)">编辑</button>
            <span v-if="ev.risk_level === 'red'" class="risk-tag red">高危</span>
          </div>
        </div>
      </div>

      <!-- ItemCompleted -->
      <div v-else-if="ev.type === 'ItemCompleted'" class="ev-card ev-item">
        <span class="ev-badge" style="background: var(--c-success)">完成</span>
        <div class="ev-body">
          <div class="ev-title">{{ ev.item_type }}</div>
          <div v-if="typeof ev.content === 'string'" class="ev-sub">{{ ev.content.slice(0, 200) }}</div>
          <div v-if="ev.rationale" class="ev-rationale" data-testid="ev-rationale">
            <span class="metric-chip">理由</span>
            <span class="ev-rationale-text">{{ ev.rationale }}</span>
            <span class="ev-rationale-src">（模型自述理由，非验证事实）</span>
          </div>
          <div v-if="ev.metrics" class="ev-metrics">
            <span v-for="(v, k) in Object.entries(ev.metrics).slice(0, 4)" :key="k" class="metric-chip">{{ v[0] }}: {{ v[1] }}</span>
          </div>
        </div>
      </div>

      <!-- TurnCancelled -->
      <div v-else-if="ev.type === 'TurnCancelled'" class="ev-card ev-warn">
        <span class="ev-badge">取消</span>
        <div class="ev-body">{{ ev.reason || '回合已取消' }}</div>
      </div>

      <!-- Rollback -->
      <div v-else-if="ev.type === 'Rollback'" class="ev-card ev-warn">
        <span class="ev-badge">回滚</span>
        <div class="ev-body">已回滚到 Checkpoint {{ ev.checkpoint_id }}</div>
      </div>

      <!-- Error -->
      <div v-else-if="ev.type === 'Error'" class="ev-card ev-error">
        <span class="ev-badge">错误</span>
        <div class="ev-body">{{ ev.message }}</div>
      </div>

      <!-- RootCauseReport -->
      <div v-else-if="ev.type === 'RootCauseReport'" class="ev-card ev-rc" data-testid="ev-root-cause">
        <span class="ev-badge" style="background: var(--c-accent, #7c5cff)">溯源</span>
        <div class="ev-body">
          <div class="ev-title">溯源报告 {{ ev.report_id }}</div>
          <div class="ev-sub">
            {{ ev.trigger }} · {{ ev.status === 'degraded' ? '纯确定性' : '完整链路' }} · {{ (ev.stages || []).length }} 阶段定位
          </div>
          <div v-for="(s, idx) in (ev.stages || []).slice(0, 3)" :key="idx" class="ev-rc-stage">[{{ s.stage }}] {{ s.issue }}</div>
          <div v-for="(a, idx) in (ev.attributions || []).slice(0, 2)" :key="idx" class="ev-rc-attr">
            {{ a.hypothesis }}
            <span v-if="a.agent_inferred" class="metric-chip">agent_inferred</span>
          </div>
        </div>
      </div>

      <!-- 默认事件 -->
      <details v-else class="ev-card ev-raw">
        <summary><span class="ev-badge">系统消息</span><span class="ev-sub">点击查看详情</span></summary>
        <div class="ev-body">{{ JSON.stringify(ev).slice(0, 300) }}</div>
      </details>
    </div>
  </div>
</template>
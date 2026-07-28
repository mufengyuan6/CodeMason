<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface InboxItem {
  item_id: string
  command: string
  reason: string
  verdict_decision: 'block' | 'escalate'
}

interface InboxData {
  enabled: boolean
  stats: {
    pending: number
    by_decision: {
      block: number
      escalate: number
    }
    total: number
  }
  items: InboxItem[]
}

const TOKEN = 'demo-token'

const data = ref<InboxData | null>(null)
const error = ref<string | null>(null)
const editing = ref<string | null>(null)
const editCmd = ref('')

const load = async () => {
  try {
    const res = await fetch('/api/inbox', { headers: { 'x-agent-token': TOKEN } })
    data.value = await res.json()
  } catch (e) {
    error.value = String(e)
  }
}

onMounted(() => {
  load()
})

const respond = async (itemId: string, decision: string, editedCommand?: string) => {
  const res = await fetch('/api/inbox/respond', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-agent-token': TOKEN },
    body: JSON.stringify({ item_id: itemId, decision, edited_command: editedCommand }),
  })
  if (!res.ok) throw new Error('处置失败')
  load()
}

const startEdit = (item: InboxItem) => {
  editing.value = item.item_id
  editCmd.value = item.command
}

const cancelEdit = () => {
  editing.value = null
  editCmd.value = ''
}

const submitEdit = (itemId: string) => {
  respond(itemId, 'edit', editCmd.value)
  cancelEdit()
}
</script>

<template>
  <div v-if="error" class="panel-error">收件箱加载失败: {{ error }}</div>
  <div v-else-if="!data" class="panel-loading">加载审批收件箱…</div>
  <div v-else-if="!data.enabled" class="panel-empty">审批收件箱未启用（后端未挂载）</div>
  <div v-else class="panel" data-testid="approval-inbox-panel">
    <h3 class="panel-title">📥 审批收件箱 <span class="title-sub">只审分类器拦截件</span></h3>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-value accent">{{ data.stats?.pending ?? 0 }}</div>
        <div class="metric-label">待处置</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.stats?.by_decision?.block ?? 0 }}</div>
        <div class="metric-label">拦截件</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.stats?.by_decision?.escalate ?? 0 }}</div>
        <div class="metric-label">存疑件</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.stats?.total ?? 0 }}</div>
        <div class="metric-label">累计</div>
      </div>
    </div>

    <div v-if="data.items?.length === 0" class="panel-empty">收件箱为空（分类器放行的动作已自动执行）</div>

    <div v-for="item in data.items" :key="item.item_id" class="approval-item">
      <div class="approval-head">
        <span :class="['risk-badge', item.verdict_decision === 'block' ? 'risk-red' : 'risk-yellow']">
          {{ item.verdict_decision === 'block' ? '⛔ 拦截' : '⚠️ 存疑' }}
        </span>
        <code class="approval-cmd">{{ item.command }}</code>
      </div>
      <div class="approval-reason">{{ item.reason }}</div>
      <div v-if="editing === item.item_id" class="approval-edit">
        <input v-model="editCmd" class="edit-input" placeholder="修改后的命令…" />
        <button class="btn btn-primary" @click="submitEdit(item.item_id)">提交修改</button>
        <button class="btn" @click="cancelEdit">取消</button>
      </div>
      <div v-else class="approval-actions">
        <button class="btn btn-success" @click="respond(item.item_id, 'approve')">批准执行</button>
        <button class="btn btn-warn" @click="startEdit(item)">修改命令</button>
        <button class="btn btn-danger" @click="respond(item.item_id, 'reject')">拒绝</button>
      </div>
    </div>
  </div>
</template>
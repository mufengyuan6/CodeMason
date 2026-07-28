<script setup lang="ts">
import { ref } from 'vue'

interface Approval {
  approval_id: string
  tool_name: string
  description: string
  command?: string
  diff_preview?: string
  risk_level: 'red' | 'yellow' | 'green'
}

interface Props {
  approvals: Approval[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  decision: [approvalId: string, decision: string, editedCommand?: string]
  viewFile: [approval: Approval]
}>()

const confirmId = ref<string | null>(null)
const editing = ref<string | null>(null)
const editValue = ref('')

const handleDecision = (a: Approval, decision: string) => {
  if (decision === 'approve' && a.risk_level === 'red') {
    // 高危操作二次确认
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
  emit('decision', a.approval_id, decision)
}

const handleEditSubmit = (a: Approval) => {
  emit('decision', a.approval_id, 'edit', editValue.value || a.command)
  editing.value = null
  editValue.value = ''
}

const startEdit = (a: Approval) => {
  editing.value = a.approval_id
  editValue.value = a.command || ''
}
</script>

<template>
  <div v-if="props.approvals.length === 0" class="approval-center empty" data-testid="approval-center">
    <p>审批队列为空</p>
    <p class="hint">危险操作会在这里等待你的确认</p>
  </div>

  <div v-else class="approval-center" data-testid="approval-center">
    <h3 class="module-title">审批中心 <span class="count-pill">{{ props.approvals.length }}</span></h3>
    <div
      v-for="a in props.approvals"
      :key="a.approval_id"
      :class="['approval-card', `risk-${a.risk_level}`]"
      data-testid="approval-card"
    >
      <div class="appr-header">
        <span :class="['risk-tag', a.risk_level]">{{ a.risk_level === 'red' ? '高危' : a.risk_level === 'yellow' ? '中危' : '低危' }}</span>
        <span class="appr-tool">{{ a.tool_name }}</span>
        <span class="appr-desc">{{ a.description }}</span>
      </div>

      <code v-if="a.command" class="appr-command">{{ a.command }}</code>

      <div v-if="a.diff_preview" class="diff-viewer" data-testid="diff-preview">
        <pre class="diff-pre"><code>{{ a.diff_preview }}</code></pre>
      </div>

      <div v-if="editing === a.approval_id" class="appr-edit">
        <textarea v-model="editValue" class="appr-edit-input" rows="2" autofocus />
        <button class="btn btn-edit" @click="handleEditSubmit(a)">提交修改</button>
        <button class="btn btn-ghost" @click="editing = null">取消</button>
      </div>
      <div v-else class="appr-actions">
        <button
          class="btn btn-approve"
          data-testid="appr-approve"
          @click="handleDecision(a, 'approve')"
        >
          {{ confirmId === a.approval_id ? '再次确认' : '批准' }}
        </button>
        <button class="btn btn-reject" data-testid="appr-reject" @click="handleDecision(a, 'reject')">拒绝</button>
        <button class="btn btn-edit" @click="startEdit(a)">编辑</button>
        <button v-if="props.approvals" class="btn btn-ghost" @click="emit('viewFile', a)">查看上下文</button>
      </div>
    </div>
  </div>
</template>
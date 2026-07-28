<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

interface Props {
  approvalId: string
  toolName: string
  command: string
  riskLevel: 'low' | 'medium' | 'high' | 'critical'
  onApprove: (id: string) => void
  onReject: (id: string, reason: string) => void
}

const props = defineProps<Props>()

const TIMEOUT_SECONDS = 30
const timeLeft = ref(TIMEOUT_SECONDS)
const rejectReason = ref('')
const showRejectInput = ref(false)
const isExpired = ref(false)
let timer: number | null = null

// 风险等级样式
const riskConfig = computed(() => {
  const configs = {
    low: { label: '低风险', color: 'var(--c-success)', bg: 'rgba(52, 211, 153, 0.1)' },
    medium: { label: '中风险', color: 'var(--c-accent)', bg: 'rgba(234, 179, 8, 0.1)' },
    high: { label: '高风险', color: 'var(--c-warning)', bg: 'rgba(249, 115, 22, 0.1)' },
    critical: { label: '危险', color: 'var(--c-danger)', bg: 'rgba(248, 113, 113, 0.1)' }
  }
  return configs[props.riskLevel]
})

// 进度百分比
const progressPercent = computed(() => {
  return (timeLeft.value / TIMEOUT_SECONDS) * 100
})

// 工具名中文映射
const toolLabel = computed(() => {
  const labels: Record<string, string> = {
    'Bash': '执行命令',
    'Write': '写入文件',
    'Edit': '编辑文件',
    'WebFetch': '访问网页',
    'WebSearch': '搜索网络',
    'Agent': '启动子代理'
  }
  return labels[props.toolName] || props.toolName
})

const handleApprove = () => {
  props.onApprove(props.approvalId)
}

const handleReject = () => {
  if (showRejectInput.value) {
    props.onReject(props.approvalId, rejectReason.value || '用户拒绝')
    showRejectInput.value = false
  } else {
    showRejectInput.value = true
  }
}

const handleRejectCancel = () => {
  showRejectInput.value = false
  rejectReason.value = ''
}

onMounted(() => {
  timer = window.setInterval(() => {
    timeLeft.value--
    if (timeLeft.value <= 0) {
      isExpired.value = true
      if (timer) clearInterval(timer)
    }
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div :class="['approval-inline', `risk-${riskLevel}`]" data-testid="approval-inline">
    <!-- 顶部：风险等级 + 倒计时 -->
    <div class="approval-header">
      <div class="risk-badge" :style="{ background: riskConfig.bg, color: riskConfig.color }">
        <span class="risk-dot" :style="{ background: riskConfig.color }" />
        {{ riskConfig.label }}
      </div>
      <div class="approval-timer">
        <svg class="timer-ring" viewBox="0 0 36 36">
          <circle class="timer-bg" cx="18" cy="18" r="16" />
          <circle
            class="timer-progress"
            cx="18"
            cy="18"
            r="16"
            :style="{ strokeDashoffset: `${100 - progressPercent}` }"
          />
        </svg>
        <span class="timer-text">{{ timeLeft }}s</span>
      </div>
    </div>

    <!-- 操作描述 -->
    <div class="approval-body">
      <div class="tool-info">
        <span class="tool-icon">⚙️</span>
        <span class="tool-name">{{ toolLabel }}</span>
      </div>
      <div class="command-preview">
        <code>{{ command }}</code>
      </div>
    </div>

    <!-- 拒绝原因输入 -->
    <div v-if="showRejectInput" class="reject-input-area">
      <textarea
        v-model="rejectReason"
        class="reject-input"
        placeholder="拒绝原因（可选）..."
        rows="2"
        data-testid="reject-reason"
      />
      <div class="reject-actions">
        <button class="btn-cancel" @click="handleRejectCancel">取消</button>
        <button class="btn-confirm-reject" @click="handleReject">确认拒绝</button>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="approval-actions">
      <button
        class="btn-reject"
        data-testid="reject-btn"
        :disabled="isExpired"
        @click="handleReject"
      >
        拒绝
      </button>
      <button
        class="btn-approve"
        data-testid="approve-btn"
        :disabled="isExpired"
        @click="handleApprove"
      >
        <span class="approve-icon">✓</span>
        通过
      </button>
    </div>

    <!-- 过期提示 -->
    <div v-if="isExpired" class="expired-notice">
      审批已超时，操作已自动拒绝
    </div>
  </div>
</template>

<style scoped>
.approval-inline {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  margin: var(--space-3) 0;
  animation: slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  position: relative;
  overflow: hidden;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 风险等级左边框 */
.approval-inline.risk-low {
  border-left: 3px solid var(--c-success);
}

.approval-inline.risk-medium {
  border-left: 3px solid var(--c-accent);
}

.approval-inline.risk-high {
  border-left: 3px solid var(--c-warning);
}

.approval-inline.risk-critical {
  border-left: 3px solid var(--c-danger);
  background: rgba(248, 113, 113, 0.03);
}

/* 顶部 */
.approval-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}

.risk-badge {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--fs-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.risk-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* 倒计时 */
.approval-timer {
  position: relative;
  width: 36px;
  height: 36px;
}

.timer-ring {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.timer-bg {
  fill: none;
  stroke: var(--c-border);
  stroke-width: 2;
}

.timer-progress {
  fill: none;
  stroke: var(--c-primary);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-dasharray: 100;
  transition: stroke-dashoffset 1s linear;
}

.timer-text {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
  color: var(--c-text-dim);
}

/* 操作描述 */
.approval-body {
  margin-bottom: var(--space-4);
}

.tool-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.tool-icon {
  font-size: 16px;
}

.tool-name {
  font-weight: 600;
  color: var(--c-text);
  font-size: var(--fs-md);
}

.command-preview {
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  overflow-x: auto;
}

.command-preview code {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  color: var(--c-text);
  white-space: pre-wrap;
  word-break: break-all;
}

/* 拒绝原因输入 */
.reject-input-area {
  margin-bottom: var(--space-3);
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.reject-input {
  width: 100%;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  color: var(--c-text);
  font-family: var(--font-body);
  font-size: var(--fs-sm);
  resize: none;
  outline: none;
}

.reject-input:focus {
  border-color: var(--c-danger);
}

.reject-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.btn-cancel {
  background: none;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-3);
  color: var(--c-text-dim);
  font-size: var(--fs-xs);
  cursor: pointer;
}

.btn-confirm-reject {
  background: var(--c-danger);
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-3);
  color: white;
  font-size: var(--fs-xs);
  font-weight: 600;
  cursor: pointer;
}

/* 操作按钮 */
.approval-actions {
  display: flex;
  gap: var(--space-3);
}

.btn-reject {
  flex: 1;
  background: none;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-4);
  color: var(--c-text-dim);
  font-weight: 500;
  font-size: var(--fs-md);
  cursor: pointer;
  transition: all 0.2s;
}

.btn-reject:hover:not(:disabled) {
  border-color: var(--c-danger);
  color: var(--c-danger);
  background: rgba(248, 113, 113, 0.05);
}

.btn-approve {
  flex: 2;
  background: var(--c-primary);
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-4);
  color: var(--c-bg);
  font-weight: 600;
  font-size: var(--fs-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  transition: all 0.2s;
}

.btn-approve:hover:not(:disabled) {
  background: var(--c-primary-soft);
  box-shadow: var(--glow-primary);
}

.btn-approve:disabled,
.btn-reject:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.approve-icon {
  font-size: 14px;
}

/* 过期提示 */
.expired-notice {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: rgba(248, 113, 113, 0.05);
  border-radius: var(--radius-sm);
  color: var(--c-danger);
  font-size: var(--fs-xs);
  text-align: center;
}
</style>

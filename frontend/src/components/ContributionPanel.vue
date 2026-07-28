<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface ContributionData {
  enabled: boolean
  report: {
    ai_involvement: 'full_auto' | 'assisted' | 'human_led'
    files: Array<{
      path: string
      line_range?: [number, number]
      changed_by: 'ai' | 'human'
      provenance_event_ids?: string[]
    }>
    cost: {
      tokens: number
      duration_s: number
    }
    verification: {
      tests_run: number
      gate_status: string
    }
  }
}

const TOKEN = 'demo-token'

const data = ref<ContributionData | null>(null)
const error = ref<string | null>(null)
const taskId = ref('task-1')

const load = async (tid: string) => {
  error.value = null
  try {
    const res = await fetch(`/api/contribution?task_id=${encodeURIComponent(tid)}`, { headers: { 'x-agent-token': TOKEN } })
    data.value = await res.json()
  } catch (e) {
    error.value = String(e)
  }
}

onMounted(() => {
  load(taskId.value)
})

const getInvolvementLabel = (involvement: string) => {
  const labels: Record<string, string> = { full_auto: '全自动', assisted: '人机协作', human_led: '人类主导' }
  return labels[involvement] || involvement
}
</script>

<template>
  <div v-if="error" class="panel-error">贡献报告加载失败: {{ error }}</div>
  <div v-else-if="!data" class="panel-loading">加载贡献报告…</div>
  <div v-else-if="!data.enabled" class="panel-empty">贡献报告未启用（后端未挂载）</div>
  <div v-else class="panel" data-testid="contribution-panel">
    <h3 class="panel-title">📜 AI 贡献审计</h3>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-value accent">{{ getInvolvementLabel(data.report.ai_involvement) }}</div>
        <div class="metric-label">AI 参与度</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.report.files?.length ?? 0 }}</div>
        <div class="metric-label">AI 修改文件</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.report.cost?.tokens ?? 0 }}</div>
        <div class="metric-label">Tokens</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ (data.report.cost?.duration_s ?? 0).toFixed(1) }}s</div>
        <div class="metric-label">耗时</div>
      </div>
    </div>

    <div class="section">
      <h4 class="section-title">文件贡献（可下钻 provenance）</h4>
      <div v-if="data.report.files?.length === 0" class="panel-empty">暂无文件贡献</div>
      <div v-for="f in data.report.files" :key="f.path" class="cost-row">
        <span class="cost-op">{{ f.path }}</span>
        <span v-if="f.line_range?.length === 2" class="cost-tokens">L{{ f.line_range[0] }}-{{ f.line_range[1] }}</span>
        <span :class="['contribution-badge', f.changed_by === 'ai' ? 'badge-ai' : 'badge-human']">{{ f.changed_by === 'ai' ? 'AI' : '协作' }}</span>
        <span class="cost-msg" :title="f.provenance_event_ids?.join(', ')">事件 {{ f.provenance_event_ids?.length ?? 0 }} 条</span>
      </div>
    </div>

    <div class="section">
      <h4 class="section-title">验证证据</h4>
      <div class="cost-row">
        <span class="cost-op">测试</span>
        <span class="cost-tokens">{{ data.report.verification?.tests_run ?? 0 }} 次</span>
        <span class="cost-msg">门禁: {{ data.report.verification?.gate_status ?? 'unknown' }}</span>
      </div>
    </div>

    <div class="section">
      <h4 class="section-title">变更归属标注（git commit metadata）</h4>
      <pre class="attribution-block">{{ `Co-Authored-By: CodeMason AI Agent
X-CodeMason-Involvement: ${data.report.ai_involvement}
X-CodeMason-Verification: ${data.report.verification?.gate_status ?? 'unknown'}` }}</pre>
    </div>
  </div>
</template>
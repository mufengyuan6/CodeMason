<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface CostData {
  enabled: boolean
  summary: {
    total_tokens_saved: number
    total_ops: number
    save_ratio: number
    high_cost_count: number
  }
  high_cost: Array<{
    op_id: string
    op_type: string
    tokens_in: number
    tokens_out: number
    warn: string
  }>
  by_op_type: Record<string, {
    count: number
    tokens: number
    saved: number
  }>
}

const TOKEN = 'demo-token'

const data = ref<CostData | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const res = await fetch('/costs', { headers: { 'x-agent-token': TOKEN } })
    data.value = await res.json()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <div v-if="error" class="panel-error">成本数据加载失败: {{ error }}</div>
  <div v-else-if="!data" class="panel-loading">加载成本台账…</div>
  <div v-else-if="!data.enabled" class="panel-empty">成本台账未启用（后端未挂载）</div>
  <div v-else class="panel" data-testid="cost-cockpit">
    <h3 class="panel-title">⚡ 成本驾驶舱</h3>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-value accent">{{ data.summary.total_tokens_saved?.toLocaleString() ?? 0 }}</div>
        <div class="metric-label">累计节省 tokens</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ data.summary.total_ops ?? 0 }}</div>
        <div class="metric-label">总操作数</div>
      </div>
      <div class="metric-card">
        <div class="metric-value">{{ ((data.summary.save_ratio ?? 0) * 100).toFixed(1) }}%</div>
        <div class="metric-label">节省率</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', (data.summary.high_cost_count ?? 0) > 0 ? 'danger' : '']">{{ data.summary.high_cost_count ?? 0 }}</div>
        <div class="metric-label">高成本预警</div>
      </div>
    </div>

    <div v-if="data.high_cost?.length > 0" class="section">
      <h4 class="section-title">⚠️ 高成本操作预警</h4>
      <div v-for="r in data.high_cost" :key="r.op_id" class="cost-row warn">
        <span class="cost-op">{{ r.op_type }}</span>
        <span class="cost-tokens">{{ (r.tokens_in + r.tokens_out).toLocaleString() }} tokens</span>
        <span class="cost-msg">{{ r.warn }}</span>
      </div>
    </div>

    <div v-if="data.by_op_type" class="section">
      <h4 class="section-title">按操作类型</h4>
      <table class="cost-table">
        <thead>
          <tr>
            <th>Op 类型</th>
            <th>次数</th>
            <th>Tokens</th>
            <th>节省</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(agg, type) in data.by_op_type" :key="type">
            <td>{{ type }}</td>
            <td>{{ agg.count }}</td>
            <td>{{ agg.tokens.toLocaleString() }}</td>
            <td class="accent">+{{ agg.saved.toLocaleString() }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface ContextData {
  enabled: boolean
  metrics: {
    recall_count: number
    recall_rate: number
    stale_hit_rate: number
    summary_misses: number
    avg_compression_ratio: number
    compression_events: number
  }
  policies: string[]
}

const TOKEN = 'demo-token'

const data = ref<ContextData | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const res = await fetch('/context', { headers: { 'x-agent-token': TOKEN } })
    data.value = await res.json()
  } catch (e) {
    error.value = String(e)
  }
})

const getPolicyTag = (p: string) => {
  if (p === 'default') return '基准'
  if (p.includes('aggressive')) return '激进遗忘'
  return '温和'
}
</script>

<template>
  <div v-if="error" class="panel-error">上下文数据加载失败: {{ error }}</div>
  <div v-else-if="!data" class="panel-loading">加载上下文指标…</div>
  <div v-else-if="!data.enabled" class="panel-empty">上下文指标未启用（后端未挂载）</div>
  <div v-else class="panel" data-testid="context-panel">
    <h3 class="panel-title">🧠 上下文管理</h3>

    <div class="metric-grid">
      <div class="metric-card">
        <div :class="['metric-value', data.metrics.recall_rate > 0.3 ? 'danger' : '']">{{ data.metrics.recall_count ?? 0 }}</div>
        <div class="metric-label">回捞次数（{{ ((data.metrics.recall_rate ?? 0) * 100).toFixed(1) }}%）</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', (data.metrics.stale_hit_rate ?? 1) > 0.05 ? 'danger' : '']">{{ ((data.metrics.stale_hit_rate ?? 0) * 100).toFixed(1) }}%</div>
        <div class="metric-label">stale 命中率</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', data.metrics.summary_misses > 0 ? 'danger' : '']">{{ data.metrics.summary_misses ?? 0 }}</div>
        <div class="metric-label">摘要遗漏</div>
      </div>
      <div class="metric-card">
        <div class="metric-value accent">{{ ((data.metrics.avg_compression_ratio ?? 0) * 100).toFixed(0) }}%</div>
        <div class="metric-label">平均压缩比（{{ data.metrics.compression_events ?? 0 }} 次）</div>
      </div>
    </div>

    <!-- 压缩策略对照子区（condenser A/B · v1.13） -->
    <div class="section">
      <h4 class="section-title">压缩策略对照（A/B）</h4>
      <p class="section-hint">同一事件流、不同 condenser 管道配置对比——把"λ 设 0.1 还是 0.3"变成评测数据。</p>
      <div class="policy-list">
        <div v-for="p in (data.policies || [])" :key="p" class="policy-chip">
          <span class="policy-name">{{ p }}</span>
          <span class="policy-tag">{{ getPolicyTag(p) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
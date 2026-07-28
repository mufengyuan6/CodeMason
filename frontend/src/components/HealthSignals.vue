<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface HealthData {
  enabled: boolean
  report: {
    level: 'healthy' | 'degraded' | 'critical'
    score: number
    stuck_count: number
    refetch_rate: number
    summary_miss: number
    stale_hit_rate: number
    advice: string
  }
}

const TOKEN = 'demo-token'

const data = ref<HealthData | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const res = await fetch('/health-signals', { headers: { 'x-agent-token': TOKEN } })
    data.value = await res.json()
  } catch (e) {
    error.value = String(e)
  }
})
</script>

<template>
  <div v-if="error" class="panel-error">健康信号加载失败: {{ error }}</div>
  <div v-else-if="!data" class="panel-loading">加载健康信号…</div>
  <div v-else-if="!data.enabled" class="panel-empty">健康信号未启用（后端未挂载）</div>
  <div v-else class="panel" data-testid="health-signals">
    <h3 class="panel-title">💓 健康信号</h3>

    <div :class="['health-level', data.report.level === 'healthy' ? 'ok' : data.report.level === 'degraded' ? 'warn' : 'danger']">
      <span class="health-dot" />
      {{ data.report.level === 'healthy' ? '会话健康' : data.report.level === 'degraded' ? '会话退化' : '会话告急' }}
      <span class="health-score">{{ Math.round(data.report.score) }}/100</span>
    </div>

    <div class="metric-grid">
      <div class="metric-card">
        <div :class="['metric-value', data.report.stuck_count > 0 ? 'warn' : '']">{{ data.report.stuck_count }}</div>
        <div class="metric-label">stuck 信号</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', data.report.refetch_rate > 0.3 ? 'danger' : '']">{{ ((data.report.refetch_rate ?? 0) * 100).toFixed(1) }}%</div>
        <div class="metric-label">回捞率</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', data.report.summary_miss > 0 ? 'warn' : '']">{{ data.report.summary_miss }}</div>
        <div class="metric-label">摘要遗漏</div>
      </div>
      <div class="metric-card">
        <div :class="['metric-value', (data.report.stale_hit_rate ?? 1) > 0.05 ? 'danger' : '']">{{ ((data.report.stale_hit_rate ?? 0) * 100).toFixed(1) }}%</div>
        <div class="metric-label">stale 命中</div>
      </div>
    </div>

    <div class="health-advice">{{ data.report.advice }}</div>
  </div>
</template>
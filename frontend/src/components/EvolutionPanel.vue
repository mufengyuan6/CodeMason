<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

interface HealthTarget {
  score: number
  total_cycles: number
  applied: number
  rolled_back: number
  success_rate: number
  rollback_rate: number
  last_improvement: number
}

interface TrendPoint {
  cycle_id: string
  timestamp: number
  signals: number
  candidates: number
  validated: number
  applied: number
  duration_ms: number
  trigger: string
}

interface EvolutionStatus {
  enabled: boolean
  registered_targets?: string[]
  cycle_count?: number
  recent_cycles?: any[]
}

interface HealthReport {
  enabled: boolean
  health?: Record<string, HealthTarget>
}

interface TrendReport {
  enabled: boolean
  trend?: TrendPoint[]
}

const TRIGGER_LABEL: Record<string, string> = {
  system_failure: '系统故障',
  user_feedback: '用户反馈',
  periodic: '定期',
  manual: '手动',
}

const TARGET_LABEL: Record<string, string> = {
  memory: '🧠 记忆',
  skill: '⚡ 技能',
  planning: '📋 规划',
  tool_usage: '🔧 工具',
  harness: '🏗️ Harness',
}

const TARGET_ICONS: Record<string, string> = {
  memory: '🧠',
  skill: '⚡',
  planning: '📋',
  tool_usage: '🔧',
  harness: '🏗️',
}

const status = ref<EvolutionStatus>({ enabled: false })
const health = ref<HealthReport>({ enabled: false })
const trend = ref<TrendReport>({ enabled: false })
const loading = ref(false)
const lastResult = ref<any>(null)

const healthTargets = computed(() => {
  if (!health.value.health) return []
  return Object.entries(health.value.health).map(([target, h]) => ({
    target,
    label: TARGET_LABEL[target] || target,
    icon: TARGET_ICONS[target] || '•',
    ...h,
  }))
})

const trendPoints = computed(() => {
  return (trend.value.trend || []).slice(-20)
})

const overallScore = computed(() => {
  if (!health.value.health) return 0
  const scores = Object.values(health.value.health).map(h => h.score)
  return scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0
})

const fetchStatus = async () => {
  try {
    const [statusResp, healthResp, trendResp] = await Promise.all([
      fetch('/api/evolution/status'),
      fetch('/api/evolution/health'),
      fetch('/api/evolution/trend'),
    ])
    status.value = await statusResp.json()
    health.value = await healthResp.json()
    trend.value = await trendResp.json()
  } catch {
    status.value = { enabled: false }
  }
}

const runEvolution = async (trigger = 'manual') => {
  loading.value = true
  try {
    const resp = await fetch('/api/evolution/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trigger, session_id: 'web' }),
    })
    const data = await resp.json()
    lastResult.value = data.result
    await fetchStatus()
  } catch (e) {
    lastResult.value = { error: String(e) }
  } finally {
    loading.value = false
  }
}

const formatDuration = (ms: number) => {
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

const formatTime = (ts: number) => {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

const scoreColor = (score: number) => {
  if (score >= 0.8) return '#10b981'
  if (score >= 0.5) return '#f59e0b'
  return '#ef4444'
}

onMounted(fetchStatus)
</script>

<template>
  <div class="panel evolution-panel" data-testid="evolution-panel">
    <h3 class="panel-title">🧬 自进化引擎</h3>

    <div v-if="!status.enabled" class="panel-empty">
      自进化引擎未挂载。启动后支持 Observe→Analyze→Improve→Verify→Persist 闭环。
    </div>

    <template v-else>
      <!-- 总体健康度 -->
      <div class="evo-overall">
        <div class="evo-overall-score" :style="{ color: scoreColor(overallScore) }">
          {{ Math.round(overallScore * 100) }}%
        </div>
        <div class="evo-overall-label">总体健康度</div>
      </div>

      <!-- 五维度健康度仪表盘 -->
      <div class="evo-health-grid">
        <div v-for="t in healthTargets" :key="t.target" class="evo-health-card" :data-testid="`health-${t.target}`">
          <div class="evo-health-header">
            <span class="evo-health-icon">{{ t.icon }}</span>
            <span class="evo-health-label">{{ t.label }}</span>
          </div>
          <div class="evo-health-bar-track">
            <div
              class="evo-health-bar-fill"
              :style="{ width: `${t.score * 100}%`, backgroundColor: scoreColor(t.score) }"
            />
          </div>
          <div class="evo-health-meta">
            <span>{{ t.applied }}/{{ t.total_cycles }} 生效</span>
            <span>{{ Math.round(t.score * 100) }}%</span>
          </div>
        </div>
      </div>

      <!-- 手动触发 -->
      <button
        class="evo-run-btn"
        :disabled="loading"
        data-testid="evo-run-btn"
        @click="runEvolution('manual')"
      >
        {{ loading ? '进化中...' : '▶ 手动触发一轮进化' }}
      </button>

      <!-- 最新结果 -->
      <div v-if="lastResult && !lastResult.error" class="evo-result" data-testid="evo-result">
        <div class="evo-result-head">
          <span class="evo-cycle-id">{{ lastResult.cycle_id }}</span>
          <span :class="['evo-status-badge', lastResult.status === 'completed' ? 'evo-ok' : 'evo-err']">
            {{ lastResult.status === 'completed' ? '✅ 完成' : '❌ 失败' }}
          </span>
        </div>
        <div class="evo-metrics">
          <span>信号 {{ lastResult.signals }}</span>
          <span>候选 {{ lastResult.candidates }}</span>
          <span>验证 {{ lastResult.validated }}</span>
          <span>生效 {{ lastResult.applied }}</span>
          <span>{{ formatDuration(lastResult.duration_ms) }}</span>
        </div>
      </div>

      <!-- 进化趋势（简化折线图） -->
      <div v-if="trendPoints.length > 0" class="evo-trend">
        <h4>📈 进化趋势</h4>
        <div class="evo-trend-chart" data-testid="evo-trend">
          <svg :viewBox="`0 0 ${Math.max(trendPoints.length * 30, 200)} 80`" preserveAspectRatio="none">
            <!-- 应用数折线 -->
            <polyline
              :points="trendPoints.map((p, i) => `${i * 30 + 15},${70 - p.applied * 20}`).join(' ')"
              fill="none"
              stroke="#10b981"
              stroke-width="2"
            />
            <!-- 验证数折线 -->
            <polyline
              :points="trendPoints.map((p, i) => `${i * 30 + 15},${70 - p.validated * 15}`).join(' ')"
              fill="none"
              stroke="#3b82f6"
              stroke-width="2"
              stroke-dasharray="4,4"
            />
            <!-- 信号数折线 -->
            <polyline
              :points="trendPoints.map((p, i) => `${i * 30 + 15},${70 - p.signals * 5}`).join(' ')"
              fill="none"
              stroke="#f59e0b"
              stroke-width="1"
              stroke-dasharray="2,2"
            />
          </svg>
          <div class="evo-trend-legend">
            <span class="evo-legend-item"><span class="evo-dot" style="background:#10b981" /> 生效</span>
            <span class="evo-legend-item"><span class="evo-dot" style="background:#3b82f6" /> 验证</span>
            <span class="evo-legend-item"><span class="evo-dot" style="background:#f59e0b" /> 信号</span>
          </div>
        </div>
      </div>

      <!-- 进化历史 -->
      <div v-if="(status.recent_cycles || []).length > 0" class="evo-history">
        <h4>🧬 进化时间线</h4>
        <div v-for="c in (status.recent_cycles || [])" :key="c.cycle_id" class="evo-cycle" :data-testid="`evo-cycle-${c.cycle_id}`">
          <div class="evo-cycle-head">
            <span class="evo-cycle-id">{{ c.cycle_id }}</span>
            <span class="evo-trigger">{{ TRIGGER_LABEL[c.trigger] || c.trigger }}</span>
            <span :class="['evo-status-badge', c.status === 'completed' ? 'evo-ok' : 'evo-err']">
              {{ c.status === 'completed' ? '✅' : '❌' }}
            </span>
          </div>
          <div class="evo-metrics">
            <span>信号 {{ c.signals }}</span>
            <span>候选 {{ c.candidates }}</span>
            <span>验证 {{ c.validated }}</span>
            <span>生效 {{ c.applied }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.evolution-panel {
  padding: 12px;
}
.evo-overall {
  text-align: center;
  padding: 8px 0 12px;
}
.evo-overall-score {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.evo-overall-label {
  font-size: 11px;
  opacity: 0.5;
  margin-top: 2px;
}
.evo-health-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-bottom: 12px;
}
.evo-health-card {
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
.evo-health-header {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 4px;
}
.evo-health-icon { font-size: 12px; }
.evo-health-label { font-size: 11px; opacity: 0.7; }
.evo-health-bar-track {
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.evo-health-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}
.evo-health-meta {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  opacity: 0.5;
  margin-top: 3px;
}
.evo-run-btn {
  display: block;
  width: 100%;
  padding: 8px;
  margin: 8px 0;
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: 6px;
  background: rgba(16, 185, 129, 0.1);
  color: #10b981;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}
.evo-run-btn:hover:not(:disabled) { background: rgba(16, 185, 129, 0.2); }
.evo-run-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.evo-result, .evo-cycle {
  padding: 8px;
  margin: 4px 0;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
.evo-result-head, .evo-cycle-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.evo-cycle-id { font-family: monospace; font-size: 12px; opacity: 0.6; }
.evo-trigger { font-size: 11px; opacity: 0.5; }
.evo-status-badge { font-size: 11px; }
.evo-ok { color: #10b981; }
.evo-err { color: #ef4444; }
.evo-metrics {
  display: flex;
  gap: 12px;
  font-size: 12px;
  opacity: 0.6;
}
.evo-trend {
  margin-top: 12px;
}
.evo-trend h4 {
  font-size: 13px;
  margin-bottom: 6px;
  opacity: 0.7;
}
.evo-trend-chart {
  background: rgba(255, 255, 255, 0.02);
  border-radius: 6px;
  padding: 8px;
  border: 1px solid rgba(255, 255, 255, 0.04);
}
.evo-trend-chart svg {
  width: 100%;
  height: 60px;
}
.evo-trend-legend {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 4px;
}
.evo-legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  opacity: 0.6;
}
.evo-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.evo-history {
  margin-top: 12px;
}
.evo-history h4 {
  font-size: 13px;
  margin-bottom: 8px;
  opacity: 0.7;
}
</style>

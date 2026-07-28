<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

interface Props {
  metrics?: Record<string, any> | null
}

const props = withDefaults(defineProps<Props>(), {
  metrics: null
})

const emit = defineEmits<{
  close: []
}>()

// metrics key → 用户语言（G-32：YAGNI 等黑话不上 UI）
const METRIC_LABELS: Record<string, string> = {
  lines_reduced: '少写代码',
  deps_added: '新增依赖',
  duplicates_found: '检出重复',
  readability_ok: '可读性达标',
}

const shown = ref(0)
let raf: number | null = null

const fmtValue = (key: string, v: any) => {
  if (key === 'readability_ok') return v ? '✓' : '—'
  if (typeof v === 'number') return Math.round(v)
  return v
}

onMounted(() => {
  if (!props.metrics) return
  const start = performance.now()
  const dur = 400
  function tick(now: number) {
    const p = Math.min(1, (now - start) / dur)
    shown.value = p
    if (p < 1) raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
})

onUnmounted(() => {
  if (raf) cancelAnimationFrame(raf)
})
</script>

<template>
  <div v-if="metrics" class="delivery-banner" data-testid="delivery-banner">
    <div class="delivery-head">
      <span class="delivery-badge">任务完成</span>
      <button class="delivery-close" aria-label="关闭交付横幅" data-testid="delivery-close" @click="emit('close')">
        ×
      </button>
    </div>
    <p v-if="metrics.lines_reduced > 0" class="delivery-summary">
      本次少写 <b>{{ Math.round(metrics.lines_reduced * shown) }}</b> 行代码
    </p>
    <div v-if="Object.keys(metrics).length > 0" class="delivery-metrics">
      <div v-for="(v, k) in metrics" :key="k" class="delivery-metric">
        <span class="delivery-metric-label">{{ METRIC_LABELS[k] || k }}</span>
        <span class="delivery-metric-value">{{ fmtValue(k, typeof v === 'number' ? v * shown : v) }}</span>
      </div>
    </div>
    <p class="delivery-hint">在下方输入下一个任务</p>
  </div>
</template>
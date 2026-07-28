<script setup lang="ts">
import { computed, ref } from 'vue'

interface Metric {
  lines_reduced?: number
  line_reduction?: number
  deps_added?: number
  new_dependencies?: number
  duplicate_impls?: number
 duplicates?: number
  readability_passed?: number
  readability?: number
  [key: string]: any
}

interface Props {
  metrics: Metric[]
}

const props = defineProps<Props>()

const expanded = ref<number | null>(null)

const totals = computed(() => {
  const t = { lines: 0, deps: 0, dups: 0, readable: 0, count: props.metrics.length }
  for (const m of props.metrics) {
    t.lines += m.lines_reduced ?? m.line_reduction ?? 0
    t.deps += m.deps_added ?? m.new_dependencies ?? 0
    t.dups += m.duplicate_impls ?? m.duplicates ?? 0
    t.readable += m.readability_passed ?? m.readability ?? 0
  }
  return t
})

const cards = computed(() => [
  { key: 'lines', label: '代码行数减少', value: totals.value.lines, unit: '行', accent: true },
  { key: 'deps', label: '依赖未新增', value: totals.value.deps, unit: '个', accent: false },
  { key: 'dups', label: '重复实现检出', value: totals.value.dups, unit: '处', accent: false },
  { key: 'readable', label: '可读性守门通过', value: totals.value.readable, unit: '项', accent: true },
])

const toggleExpand = (i: number) => {
  expanded.value = expanded.value === i ? null : i
}
</script>

<template>
  <div v-if="props.metrics.length === 0" class="yagni-board empty" data-testid="yagni-board">
    <h3 class="module-title">YAGNI 约束看板</h3>
    <p>暂无任务级数据</p>
    <p class="hint">运行一个任务后，这里会展示「写得更少」的四维量化证明</p>
  </div>

  <div v-else class="yagni-board" data-testid="yagni-board">
    <h3 class="module-title">YAGNI 约束看板 <span class="count-pill">{{ totals.count }} 任务</span></h3>
    <div class="yagni-cards">
      <div
        v-for="c in cards"
        :key="c.key"
        :class="['yagni-card', { 'is-accent': c.accent }]"
      >
        <div class="yagni-value">{{ c.value }}<span class="yagni-unit">{{ c.unit }}</span></div>
        <div class="yagni-label">{{ c.label }}</div>
      </div>
    </div>

    <div class="yagni-tasks">
      <div v-for="(m, i) in props.metrics" :key="i" class="yagni-task">
        <button class="yagni-task-head" @click="toggleExpand(i)">
          <span>任务 #{{ i + 1 }}</span>
          <span class="chevron">{{ expanded === i ? '▾' : '▸' }}</span>
        </button>
        <pre v-if="expanded === i" class="yagni-raw"><code>{{ JSON.stringify(m, null, 2) }}</code></pre>
      </div>
    </div>
  </div>
</template>
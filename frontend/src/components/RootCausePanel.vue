<script setup lang="ts">
import { computed } from 'vue'
import type { Event } from '../api'

interface Props {
  events?: Event[]
}

const props = withDefaults(defineProps<Props>(), {
  events: () => []
})

const TRIGGER_LABEL: Record<string, string> = {
  verify_failed: '验证失败',
  error: '错误事件',
  user_query: '用户疑问',
}

const reports = computed(() => {
  if (!Array.isArray(props.events)) return []
  return props.events
    .filter((ev) => ev.type === 'RootCauseReport')
    .map((ev) => ({
      id: ev.id,
      report_id: ev.report_id,
      trigger: ev.trigger,
      status: ev.status,
      trigger_event_id: ev.trigger_event_id,
      stages: ev.stages || [],
      attributions: ev.attributions || [],
      fix_instructions: ev.fix_instructions || [],
      evidence: ev.evidence || {},
      ts: ev.ts,
    }))
    .reverse() // 最新在前
})

const getEvidenceCategories = (evidence: Record<string, any>) => {
  return Object.keys(evidence).filter((k) => evidence[k]?.length).length
}
</script>

<template>
  <div class="panel" data-testid="root-cause-panel">
    <h3 class="panel-title">🔍 溯源报告</h3>
    <div v-if="reports.length === 0" class="panel-empty">
      暂无溯源报告。失败/疑问会触发根因分析（G20）——确定性证据链 + 归因假设，溯源报告落事件流可审计。
    </div>
    <div v-for="r in reports" :key="r.report_id" class="rc-entry" :data-testid="`rc-entry-${r.report_id}`">
      <div class="rc-head">
        <span class="rc-trigger">{{ TRIGGER_LABEL[r.trigger] || r.trigger }}</span>
        <span :class="['rc-status', r.status === 'completed' ? 'rc-ok' : 'rc-degraded']">
          {{ r.status === 'completed' ? '完整链路' : '纯确定性' }}
        </span>
        <span class="rc-id">{{ r.report_id }}</span>
      </div>

      <div v-if="r.stages.length > 0" class="rc-section">
        <b>阶段定位（TRAJEVAL 口径）</b>
        <ul class="rc-list">
          <li v-for="(s, i) in r.stages" :key="i" class="rc-stage" data-testid="rc-stage">
            <span :class="['rc-stage-badge', `rc-stage-${s.stage}`]">{{ s.stage }}</span>
            <span class="rc-stage-issue">{{ s.issue }}</span>
            <span v-if="typeof s.confidence === 'number'" class="rc-stage-conf">{{ (s.confidence * 100).toFixed(0) }}%</span>
          </li>
        </ul>
      </div>

      <div v-if="r.attributions.length > 0" class="rc-section">
        <b>归因假设</b>
        <ul class="rc-list">
          <li v-for="(a, i) in r.attributions" :key="i" class="rc-attribution" data-testid="rc-attribution">
            <span>{{ a.hypothesis }}</span>
            <span v-if="a.agent_inferred" class="metric-chip">agent_inferred</span>
            <span v-if="typeof a.confidence === 'number'" class="rc-conf">{{ (a.confidence * 100).toFixed(0) }}%</span>
          </li>
        </ul>
      </div>

      <div v-if="r.fix_instructions.length > 0" class="rc-section">
        <b>修复指令（机读可消费）</b>
        <ul class="rc-list">
          <li v-for="(ins, i) in r.fix_instructions" :key="i" class="rc-fix" data-testid="rc-fix">{{ ins }}</li>
        </ul>
      </div>

      <details v-if="Object.keys(r.evidence).length > 0" class="rc-evidence">
        <summary>证据链摘要（{{ getEvidenceCategories(r.evidence) }} 类）</summary>
        <pre class="rc-evidence-pre">{{ JSON.stringify(r.evidence, null, 2).slice(0, 800) }}</pre>
      </details>
    </div>
  </div>
</template>
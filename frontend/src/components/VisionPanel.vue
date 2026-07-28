<script setup lang="ts">
import { computed } from 'vue'
import type { Event } from '../api'

interface VisionEntry {
  id: string
  tool: string
  path: string
  description: string
  text: string
  blocks: Array<{
    text: string
    confidence?: number
  }>
  status: string
  ts: number | null
}

interface Props {
  events?: Event[]
}

const props = withDefaults(defineProps<Props>(), {
  events: () => []
})

/**
 * 从事件流中提取视觉工具结果（ReadImage / Ocr）。
 * 事件结构：ItemCompleted / ToolResult 事件携带工具名与输出。
 */
const extractVisionEntries = (events: Event[]): VisionEntry[] => {
  if (!Array.isArray(events)) return []
  const out: VisionEntry[] = []
  for (const ev of events) {
    const payload = ev.payload || ev
    const tool = payload.tool || payload.tool_name || ''
    if (tool === 'ReadImage' || tool === 'Ocr') {
      const result = payload.result || payload.output || {}
      out.push({
        id: String(ev.id || `${tool}-${out.length}`),
        tool,
        path: result.path || payload.path || '',
        description: result.description || '',
        text: result.text || '',
        blocks: Array.isArray(result.blocks) ? result.blocks : [],
        status: result.status || payload.status || 'unknown',
        ts: ev.ts || payload.ts || null,
      })
    }
  }
  return out
}

const entries = computed(() => extractVisionEntries(props.events))

const TOOL_LABEL: Record<string, string> = { ReadImage: '视觉子代理', Ocr: 'OCR 读字' }
const STATUS_CLASS: Record<string, string> = { ok: 'vision-ok', error: 'vision-error', unknown: 'vision-unknown' }

const getFileName = (path: string) => path.split(/[\\/]/).pop()
</script>

<template>
  <div class="panel" data-testid="vision-panel">
    <h3 class="panel-title">👁 视觉执行</h3>
    <div v-if="entries.length === 0" class="panel-empty">暂无视觉工具调用（Agent 调用 ReadImage / Ocr 后这里可见）</div>
    <div
      v-for="e in entries"
      :key="e.id"
      :class="['vision-entry', STATUS_CLASS[e.status] || '']"
      :data-testid="`vision-entry-${e.tool}`"
    >
      <div class="vision-head">
        <span class="vision-tool">{{ TOOL_LABEL[e.tool] || e.tool }}</span>
        <span v-if="e.path" class="vision-path" :title="e.path">{{ getFileName(e.path) }}</span>
        <span class="vision-status">{{ e.status }}</span>
      </div>
      <div v-if="e.description" class="vision-desc" data-testid="vision-description">{{ e.description }}</div>
      <div v-if="e.text" class="vision-ocr">
        <div class="vision-ocr-label">OCR 文字块（{{ e.blocks.length }} 块）</div>
        <pre class="vision-ocr-text">{{ e.text }}</pre>
      </div>
      <div v-if="e.blocks?.length > 0" class="vision-blocks">
        <div v-for="(b, i) in e.blocks.slice(0, 20)" :key="i" class="vision-block-row">
          <span class="vision-block-text">{{ b.text || '' }}</span>
          <span v-if="typeof b.confidence === 'number'" class="vision-block-conf">{{ (b.confidence * 100).toFixed(0) }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>
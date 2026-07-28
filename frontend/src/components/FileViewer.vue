<script setup lang="ts">
import { computed } from 'vue'
import type { Event } from '../api'

interface FileItem {
  path: string
  ev: Event
}

interface Props {
  file?: FileItem | null
  events: Event[]
}

const props = withDefaults(defineProps<Props>(), {
  file: null
})

const emit = defineEmits<{
  select: [file: FileItem | null]
}>()

// 从事件流中收集文件相关条目（ItemCompleted 中的文件路径）
const files = computed(() => {
  const seen = new Set<string>()
  const list: FileItem[] = []
  for (const ev of props.events) {
    const path = ev.file_path || ev.content?.file_path
    if (path && !seen.has(path)) {
      seen.add(path)
      list.push({ path, ev })
    }
  }
  return list
})

const content = computed(() => {
  if (!props.file) return ''
  return props.file.ev.content?.preview ?? props.file.ev.diff_preview ?? ''
})

const filePath = computed(() => {
  if (!props.file) return ''
  return props.file.path ?? props.file.ev.file_path ?? 'context'
})
</script>

<template>
  <div v-if="!file" class="file-viewer empty" data-testid="file-viewer">
    <h3 class="module-title">只读代码查看器</h3>
    <p>从事件流选择文件查看上下文</p>
    <ul v-if="files.length > 0" class="file-list">
      <li v-for="f in files" :key="f.path">
        <button class="file-item" @click="emit('select', f)">
          {{ f.path }}
        </button>
      </li>
    </ul>
  </div>

  <div v-else class="file-viewer" data-testid="file-viewer">
    <div class="file-viewer-head">
      <button class="btn btn-ghost" @click="emit('select', null)">← 返回</button>
      <code class="file-path">{{ filePath }}</code>
    </div>
    <div class="file-content">
      <pre><code>{{ content || '// 只读预览：无内容' }}</code></pre>
    </div>
  </div>
</template>
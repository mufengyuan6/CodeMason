<script setup lang="ts">
import { ref } from 'vue'

interface Session {
  session_id: string
  events: number
}

interface Props {
  sessions: Session[]
  activeId?: string
}

withDefaults(defineProps<Props>(), {
  activeId: ''
})

const emit = defineEmits<{
  select: [sessionId: string]
  new: [name: string]
}>()

const creating = ref(false)
const name = ref('')

const submit = () => {
  const n = name.value.trim()
  if (!n) return
  emit('new', n)
  creating.value = false
  name.value = ''
}
</script>

<template>
  <aside class="session-rail" data-testid="session-rail">
    <div class="rail-header">
      <span class="rail-title">会话</span>
      <button class="rail-new" aria-label="新建会话" data-testid="session-new" @click="creating = !creating">
        {{ creating ? '×' : '+' }}
      </button>
    </div>
    <div v-if="creating" class="rail-create">
      <input
        v-model="name"
        class="rail-input"
        placeholder="会话名"
        autofocus
        data-testid="session-name-input"
        @keydown.enter="submit"
      />
      <button class="btn btn-primary btn-xs" data-testid="session-create-ok" @click="submit">
        创建
      </button>
    </div>
    <div class="rail-list">
      <p v-if="sessions.length === 0" class="rail-empty">暂无会话</p>
      <button
        v-for="s in sessions"
        :key="s.session_id"
        :class="['rail-item', { 'is-active': s.session_id === activeId }]"
        :data-testid="`session-item-${s.session_id}`"
        @click="emit('select', s.session_id)"
      >
        <span class="rail-item-name">{{ s.session_id }}</span>
        <span class="rail-item-meta">{{ s.events }} 条</span>
      </button>
    </div>
  </aside>
</template>
<script setup lang="ts">
/**
 * 确认弹层：不可逆操作（压缩/取消回合）最小化误触
 * - 压缩：可审计可回读提示
 * - 取消：已落盘变更保留提示
 */
interface Props {
  title: string
  desc: string
  confirmLabel?: string
}

withDefaults(defineProps<Props>(), {
  confirmLabel: '确认'
})

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <div class="confirm-overlay" data-testid="confirm-dialog" @click="emit('cancel')">
    <div class="confirm-box" @click.stop>
      <h3 class="confirm-title">{{ title }}</h3>
      <p class="confirm-desc">{{ desc }}</p>
      <div class="confirm-actions">
        <button class="btn btn-ghost" data-testid="confirm-cancel" @click="emit('cancel')">
          取消
        </button>
        <button class="btn btn-danger" data-testid="confirm-ok" @click="emit('confirm')">
          {{ confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>
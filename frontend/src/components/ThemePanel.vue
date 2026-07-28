<script setup lang="ts">
import { ref, watch } from 'vue'
import { THEMES, applyTheme, loadTheme, type ThemeConfig } from '../theme'

/**
 * 主题面板（换壁纸式个性化，对标 Codex 换皮市场）
 * - 3 套内置主题 swatch 选择
 * - 自定义主色/强调色 color picker
 * - localStorage 持久化
 */
interface Props {
  onClose: () => void
}

const props = defineProps<Props>()

const cfg = ref<ThemeConfig>(loadTheme())

watch(cfg, (newCfg) => {
  applyTheme(newCfg)
}, { immediate: true })

const pickTheme = (id: string) => {
  cfg.value = { ...cfg.value, id }
}

const setCustom = (key: 'primary' | 'accent', value: string | null) => {
  cfg.value = { ...cfg.value, [key]: value }
}

const reset = () => {
  cfg.value = { id: 'jade-gold', primary: null, accent: null }
}
</script>

<template>
  <div class="theme-overlay" data-testid="theme-panel" @click="props.onClose">
    <div class="theme-panel" @click.stop>
      <div class="theme-head">
        <h3 class="theme-title">主题</h3>
        <button class="theme-close" aria-label="关闭主题面板" data-testid="theme-close" @click="props.onClose">
          ×
        </button>
      </div>

      <div class="theme-grid">
        <button
          v-for="t in THEMES"
          :key="t.id"
          :class="['theme-card', { 'is-active': cfg.id === t.id }]"
          :data-testid="`theme-${t.id}`"
          @click="pickTheme(t.id)"
        >
          <span class="theme-swatches">
            <span v-for="c in t.swatch" :key="c" class="theme-swatch" :style="{ background: c }" />
          </span>
          <span class="theme-name">{{ t.name }}</span>
          <span class="theme-desc">{{ t.desc }}</span>
        </button>
      </div>

      <div class="theme-custom">
        <span class="theme-custom-label">自定义颜色</span>
        <label class="theme-pick">
          主色
          <input
            type="color"
            :value="cfg.primary || '#10B981'"
            data-testid="theme-primary-pick"
            @input="setCustom('primary', ($event.target as HTMLInputElement).value)"
          />
        </label>
        <label class="theme-pick">
          强调色
          <input
            type="color"
            :value="cfg.accent || '#EAB308'"
            data-testid="theme-accent-pick"
            @input="setCustom('accent', ($event.target as HTMLInputElement).value)"
          />
        </label>
      </div>

      <div class="theme-foot">
        <button class="btn btn-ghost btn-xs" data-testid="theme-reset" @click="reset">
          恢复默认
        </button>
      </div>
    </div>
  </div>
</template>
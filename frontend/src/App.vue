<script setup lang="ts">
import { ref } from 'vue'
import LandingPage from './components/LandingPage.vue'
import Cockpit from './components/Cockpit.vue'
import { loadTheme } from './theme'

// 渲染前恢复主题（防闪烁：data-theme 先于 Vue 挂载设置）
loadTheme()

// 视图状态管理
const getInitialView = (): 'cockpit' | 'landing' => {
  try {
    return sessionStorage.getItem('codemason-view') === 'cockpit' ? 'cockpit' : 'landing'
  } catch {
    return 'landing'
  }
}

const view = ref<'cockpit' | 'landing'>(getInitialView())

const enterApp = () => {
  try {
    sessionStorage.setItem('codemason-view', 'cockpit')
  } catch {
    /* ignore */
  }
  view.value = 'cockpit'
}

const backToLanding = () => {
  try {
    sessionStorage.removeItem('codemason-view')
  } catch {
    /* ignore */
  }
  view.value = 'landing'
}
</script>

<template>
  <Cockpit v-if="view === 'cockpit'" @back="backToLanding" />
  <LandingPage v-else @enter-app="enterApp" />
</template>
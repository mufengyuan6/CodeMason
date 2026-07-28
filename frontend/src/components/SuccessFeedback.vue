<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'

interface Props {
  linesReduced?: number
  testsPassed?: number
  duration?: number
  onViewTimeline?: () => void
  onDismiss?: () => void
}

const props = withDefaults(defineProps<Props>(), {
  linesReduced: 0,
  testsPassed: 0,
  duration: 0,
})

// 数字动画状态
const displayLines = ref(0)
const displayTests = ref(0)
const showCheckmark = ref(false)
const showStats = ref(false)
const showActions = ref(false)

// 格式化时间
const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds}秒`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return secs > 0 ? `${mins}分${secs}秒` : `${mins}分钟`
}

// 数字递增动画
const animateNumber = (target: number, setter: (v: number) => void, duration: number = 800) => {
  const start = performance.now()
  const animate = (now: number) => {
    const elapsed = now - start
    const progress = Math.min(elapsed / duration, 1)
    // easeOutExpo
    const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress)
    setter(Math.round(target * eased))
    if (progress < 1) {
      requestAnimationFrame(animate)
    }
  }
  requestAnimationFrame(animate)
}

onMounted(() => {
  // 序列动画
  setTimeout(() => {
    showCheckmark.value = true
  }, 100)

  setTimeout(() => {
    showStats.value = true
    animateNumber(props.linesReduced, (v) => { displayLines.value = v })
    animateNumber(props.testsPassed, (v) => { displayTests.value = v }, 600)
  }, 500)

  setTimeout(() => {
    showActions.value = true
  }, 1200)
})
</script>

<template>
  <div class="success-feedback" data-testid="success-feedback">
    <!-- 对勾动画 -->
    <div :class="['checkmark-container', { 'is-visible': showCheckmark }]">
      <svg class="checkmark-svg" viewBox="0 0 52 52">
        <circle class="checkmark-circle" cx="26" cy="26" r="25" fill="none" />
        <path class="checkmark-check" fill="none" d="M14.1 27.2l7.1 7.2 16.7-16.8" />
      </svg>
      <span class="success-label">任务完成</span>
    </div>

    <!-- 统计数据 -->
    <div :class="['stats-grid', { 'is-visible': showStats }]">
      <div class="stat-card">
        <div class="stat-value stat-lines">
          <span class="stat-number">{{ displayLines }}</span>
          <span class="stat-unit">行</span>
        </div>
        <div class="stat-label">代码减少</div>
        <div class="stat-sublabel">YAGNI 约束</div>
      </div>
      <div class="stat-card">
        <div class="stat-value stat-tests">
          <span class="stat-number">{{ displayTests }}</span>
          <span class="stat-unit">个</span>
        </div>
        <div class="stat-label">测试通过</div>
        <div class="stat-sublabel">验证完成</div>
      </div>
      <div class="stat-card">
        <div class="stat-value stat-time">
          <span class="stat-number">{{ formatDuration(duration) }}</span>
        </div>
        <div class="stat-label">执行耗时</div>
        <div class="stat-sublabel">端到端</div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div :class="['feedback-actions', { 'is-visible': showActions }]">
      <button
        v-if="onViewTimeline"
        class="btn-view-timeline"
        data-testid="view-timeline-btn"
        @click="onViewTimeline"
      >
        <span class="btn-icon">📋</span>
        查看时间线详情
      </button>
      <button
        v-if="onDismiss"
        class="btn-dismiss"
        data-testid="dismiss-btn"
        @click="onDismiss"
      >
        继续下一个任务
      </button>
    </div>

    <!-- 装饰粒子 -->
    <div class="particles">
      <div v-for="i in 12" :key="i" class="particle" :style="{ '--i': i }" />
    </div>
  </div>
</template>

<style scoped>
.success-feedback {
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.08), rgba(52, 211, 153, 0.04));
  border: 1px solid rgba(16, 185, 129, 0.2);
  border-radius: var(--radius-lg);
  padding: var(--space-8) var(--space-6);
  text-align: center;
  position: relative;
  overflow: hidden;
  animation: fadeInUp 0.5s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 对勾动画 */
.checkmark-container {
  margin-bottom: var(--space-6);
  opacity: 0;
  transform: scale(0.8);
  transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1);
}

.checkmark-container.is-visible {
  opacity: 1;
  transform: scale(1);
}

.checkmark-svg {
  width: 64px;
  height: 64px;
  display: block;
  margin: 0 auto var(--space-3);
}

.checkmark-circle {
  stroke: var(--c-primary);
  stroke-width: 2;
  stroke-dasharray: 157;
  stroke-dashoffset: 157;
  animation: circleDraw 0.6s ease forwards 0.2s;
}

@keyframes circleDraw {
  to {
    stroke-dashoffset: 0;
  }
}

.checkmark-check {
  stroke: var(--c-primary);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 48;
  stroke-dashoffset: 48;
  animation: checkDraw 0.4s ease forwards 0.6s;
}

@keyframes checkDraw {
  to {
    stroke-dashoffset: 0;
  }
}

.success-label {
  font-family: var(--font-display);
  font-size: var(--fs-xl);
  font-weight: 700;
  color: var(--c-primary);
}

/* 统计数据 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  opacity: 0;
  transform: translateY(12px);
  transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.3s;
}

.stats-grid.is-visible {
  opacity: 1;
  transform: translateY(0);
}

.stat-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}

.stat-value {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 2px;
  margin-bottom: var(--space-1);
}

.stat-number {
  font-family: var(--font-display);
  font-size: var(--fs-2xl);
  font-weight: 700;
  color: var(--c-text);
}

.stat-unit {
  font-size: var(--fs-sm);
  color: var(--c-text-dim);
}

.stat-lines .stat-number {
  color: var(--c-primary);
}

.stat-tests .stat-number {
  color: var(--c-success);
}

.stat-time .stat-number {
  font-size: var(--fs-lg);
  color: var(--c-accent);
}

.stat-label {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--c-text);
  margin-bottom: 2px;
}

.stat-sublabel {
  font-size: var(--fs-xs);
  color: var(--c-text-dim);
}

/* 操作按钮 */
.feedback-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  align-items: center;
  opacity: 0;
  transform: translateY(8px);
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.feedback-actions.is-visible {
  opacity: 1;
  transform: translateY(0);
}

.btn-view-timeline {
  background: var(--c-primary);
  color: var(--c-bg);
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-3) var(--space-6);
  font-weight: 600;
  font-size: var(--fs-md);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  transition: all 0.2s;
}

.btn-view-timeline:hover {
  background: var(--c-primary-soft);
  box-shadow: var(--glow-primary);
  transform: translateY(-1px);
}

.btn-icon {
  font-size: 16px;
}

.btn-dismiss {
  background: none;
  border: none;
  color: var(--c-text-dim);
  font-size: var(--fs-sm);
  cursor: pointer;
  padding: var(--space-2);
  transition: color 0.2s;
}

.btn-dismiss:hover {
  color: var(--c-text);
}

/* 装饰粒子 */
.particles {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.particle {
  position: absolute;
  width: 4px;
  height: 4px;
  background: var(--c-primary);
  border-radius: 50%;
  opacity: 0;
  animation: particleBurst 1s ease forwards;
  animation-delay: calc(var(--i) * 0.05s);
}

@keyframes particleBurst {
  0% {
    opacity: 1;
    transform: translate(0, 0) scale(1);
  }
  100% {
    opacity: 0;
    transform: translate(
      calc(cos(calc(var(--i) * 30deg)) * 80px),
      calc(sin(calc(var(--i) * 30deg)) * 80px)
    ) scale(0);
  }
}
</style>

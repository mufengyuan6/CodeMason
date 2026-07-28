<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface Props {
  onDismiss: () => void
  onUseExample: (task: string) => void
}

const props = defineProps<Props>()

// 三步引导
const steps = [
  {
    step: 1,
    title: '描述你的任务',
    desc: '用自然语言告诉 Agent 你想做什么',
    example: '帮我重构登录模块，添加 OAuth2 支持'
  },
  {
    step: 2,
    title: 'Agent 自主执行',
    desc: '规划 → 编码 → 验证，全程可视化',
    example: null
  },
  {
    step: 3,
    title: '审查并交付',
    desc: '查看变更，一键确认或提出修改',
    example: null
  }
]

const currentStep = ref(1)
const inputTask = ref('')
const inputFocused = ref(false)

const handleNext = () => {
  if (currentStep.value < 3) {
    currentStep.value++
  }
}

const handlePrev = () => {
  if (currentStep.value > 1) {
    currentStep.value--
  }
}

const handleUseExample = () => {
  props.onUseExample(steps[0].example!)
}

const handleStart = () => {
  if (inputTask.value.trim()) {
    props.onUseExample(inputTask.value.trim())
  } else {
    props.onUseExample(steps[0].example!)
  }
}

const handleSkip = () => {
  props.onDismiss()
}
</script>

<template>
  <div class="first-run-overlay" data-testid="first-run-overlay">
    <div class="first-run-card" data-testid="first-run-card">
      <!-- 关闭按钮 -->
      <button class="close-btn" data-testid="first-run-close" @click="handleSkip" aria-label="跳过引导">
        ×
      </button>

      <!-- 标题 -->
      <div class="fr-header">
        <div class="fr-logo">CM</div>
        <h2 class="fr-title">欢迎使用 CodeMason</h2>
        <p class="fr-subtitle">3 步完成你的第一个任务</p>
      </div>

      <!-- 进度指示器 -->
      <div class="fr-progress">
        <div
          v-for="s in steps"
          :key="s.step"
          :class="['progress-dot', { 'is-active': s.step === currentStep, 'is-done': s.step < currentStep }]"
        >
          <span v-if="s.step < currentStep" class="check-icon">✓</span>
          <span v-else>{{ s.step }}</span>
        </div>
        <div class="progress-line">
          <div class="progress-fill" :style="{ width: `${((currentStep - 1) / 2) * 100}%` }" />
        </div>
      </div>

      <!-- 步骤内容 -->
      <div class="fr-content">
        <transition name="fade" mode="out-in">
          <div :key="currentStep" class="step-content">
            <h3 class="step-title">{{ steps[currentStep - 1].title }}</h3>
            <p class="step-desc">{{ steps[currentStep - 1].desc }}</p>

            <!-- 步骤1：任务输入 -->
            <div v-if="currentStep === 1" class="task-input-area">
              <textarea
                v-model="inputTask"
                class="task-input"
                placeholder="输入你想让 Agent 做的任务..."
                rows="3"
                data-testid="first-run-input"
                @focus="inputFocused = true"
                @blur="inputFocused = false"
              />
              <button class="use-example-btn" data-testid="use-example" @click="handleUseExample">
                使用示例任务
              </button>
            </div>

            <!-- 步骤2：执行预览 -->
            <div v-if="currentStep === 2" class="execution-preview">
              <div class="preview-item">
                <span class="preview-icon">📋</span>
                <span>Agent 分析需求并制定计划</span>
              </div>
              <div class="preview-item">
                <span class="preview-icon">✏️</span>
                <span>自动编写代码</span>
              </div>
              <div class="preview-item">
                <span class="preview-icon">✅</span>
                <span>运行测试验证</span>
              </div>
            </div>

            <!-- 步骤3：审查交付 -->
            <div v-if="currentStep === 3" class="delivery-preview">
              <div class="preview-item">
                <span class="preview-icon">🔍</span>
                <span>查看代码变更</span>
              </div>
              <div class="preview-item">
                <span class="preview-icon">👍</span>
                <span>一键确认或要求修改</span>
              </div>
              <div class="preview-item">
                <span class="preview-icon">📊</span>
                <span>查看 YAGNI 代码减少量</span>
              </div>
            </div>
          </div>
        </transition>
      </div>

      <!-- 底部按钮 -->
      <div class="fr-actions">
        <button v-if="currentStep > 1" class="btn-secondary" data-testid="first-run-prev" @click="handlePrev">
          上一步
        </button>
        <div v-else />
        <div class="fr-actions-right">
          <button class="btn-tertiary" data-testid="first-run-skip" @click="handleSkip">
            跳过引导
          </button>
          <button
            v-if="currentStep < 3"
            class="btn-primary"
            data-testid="first-run-next"
            @click="handleNext"
          >
            下一步
          </button>
          <button
            v-else
            class="btn-primary"
            data-testid="first-run-start"
            @click="handleStart"
          >
            开始任务
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.first-run-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.first-run-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  padding: var(--space-8);
  width: 480px;
  max-width: 90vw;
  position: relative;
  animation: slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

@keyframes slideUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.close-btn {
  position: absolute;
  top: var(--space-4);
  right: var(--space-4);
  background: none;
  border: none;
  color: var(--c-text-dim);
  font-size: 24px;
  cursor: pointer;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  transition: all 0.2s;
}

.close-btn:hover {
  background: var(--c-surface-2);
  color: var(--c-text);
}

.fr-header {
  text-align: center;
  margin-bottom: var(--space-6);
}

.fr-logo {
  width: 48px;
  height: 48px;
  background: var(--c-primary);
  color: var(--c-bg);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 18px;
  margin: 0 auto var(--space-4);
}

.fr-title {
  font-family: var(--font-display);
  font-size: var(--fs-2xl);
  font-weight: 700;
  color: var(--c-text);
  margin-bottom: var(--space-2);
}

.fr-subtitle {
  color: var(--c-text-dim);
  font-size: var(--fs-md);
}

/* 进度指示器 */
.fr-progress {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  margin-bottom: var(--space-8);
  position: relative;
}

.progress-dot {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--c-surface-2);
  border: 2px solid var(--c-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--c-text-dim);
  position: relative;
  z-index: 2;
  transition: all 0.3s;
}

.progress-dot.is-active {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: var(--c-bg);
  box-shadow: var(--glow-primary);
}

.progress-dot.is-done {
  background: var(--c-primary-soft);
  border-color: var(--c-primary-soft);
  color: var(--c-bg);
}

.check-icon {
  font-size: 14px;
}

.progress-line {
  position: absolute;
  top: 50%;
  left: 60px;
  right: 60px;
  height: 2px;
  background: var(--c-border);
  transform: translateY(-50%);
  z-index: 1;
}

.progress-fill {
  height: 100%;
  background: var(--c-primary);
  transition: width 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

/* 步骤内容 */
.fr-content {
  min-height: 160px;
  margin-bottom: var(--space-6);
}

.step-content {
  text-align: center;
}

.step-title {
  font-family: var(--font-display);
  font-size: var(--fs-xl);
  font-weight: 600;
  color: var(--c-text);
  margin-bottom: var(--space-2);
}

.step-desc {
  color: var(--c-text-dim);
  font-size: var(--fs-md);
  margin-bottom: var(--space-6);
}

/* 任务输入 */
.task-input-area {
  text-align: left;
}

.task-input {
  width: 100%;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  color: var(--c-text);
  font-family: var(--font-body);
  font-size: var(--fs-md);
  resize: none;
  transition: border-color 0.2s;
  outline: none;
}

.task-input:focus {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15);
}

.task-input::placeholder {
  color: var(--c-text-dim);
}

.use-example-btn {
  margin-top: var(--space-3);
  background: none;
  border: 1px dashed var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  color: var(--c-primary);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: all 0.2s;
}

.use-example-btn:hover {
  border-color: var(--c-primary);
  background: rgba(16, 185, 129, 0.05);
}

/* 预览列表 */
.execution-preview,
.delivery-preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  text-align: left;
  max-width: 320px;
  margin: 0 auto;
}

.preview-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--c-bg);
  border-radius: var(--radius-sm);
  border: 1px solid var(--c-border);
}

.preview-icon {
  font-size: 18px;
}

/* 底部按钮 */
.fr-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.fr-actions-right {
  display: flex;
  gap: var(--space-3);
}

.btn-primary {
  background: var(--c-primary);
  color: var(--c-bg);
  border: none;
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-5);
  font-weight: 600;
  font-size: var(--fs-md);
  cursor: pointer;
  transition: all 0.2s;
}

.btn-primary:hover {
  background: var(--c-primary-soft);
  box-shadow: var(--glow-primary);
}

.btn-secondary {
  background: var(--c-surface-2);
  color: var(--c-text);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-5);
  font-size: var(--fs-md);
  cursor: pointer;
  transition: all 0.2s;
}

.btn-secondary:hover {
  background: var(--c-border);
}

.btn-tertiary {
  background: none;
  border: none;
  color: var(--c-text-dim);
  font-size: var(--fs-md);
  cursor: pointer;
  padding: var(--space-2) var(--space-3);
  transition: color 0.2s;
}

.btn-tertiary:hover {
  color: var(--c-text);
}

/* 动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.fade-enter-from {
  opacity: 0;
  transform: translateX(10px);
}

.fade-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}
</style>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

/* ─── 资产 URL（沿用源项目的 mux 视频与贴纸） ─── */
const VIDEO_URL_1 = 'https://stream.mux.com/W2NRcV6MrewS7QyWWqAWZvJR9jrnPU5rxymlPg01gRzk.m3u8'
const VIDEO_URL_2 = 'https://stream.mux.com/aypDi1exkKgYKEbWme9Csi47zxIim0101hw3ghmSzQIyw.m3u8'

const STICKER1 =
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/d9a6de619b1e7bf4b31b22e6d29324306ee68ad9.d9a6de61.png'
const STICKER2 =
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/7d1d8f4421fc4780ec85b4153ca6605a4b90dd65.7d1d8f44.png'
const STICKER3 =
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/80809d23ccb460d0db21f77bb3afef67d3ad1d9a.80809d23.png'
const STICKER4 =
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/50d6c27f67bc10d6859cf37d2f017bc406ad3a0d.50d6c27f.png'

const TRAIL_STICKERS = [
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/b77ef81dabfca9ce4a4d1af5d553e17019a0d229.b77ef81d.png',
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/9ece3a6bf6c5cecf6c0078d022a171bc93baf9c5.9ece3a6b.png',
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/41b9f0bffb2c0b2e1d3fbe26c124ed1378970c35.41b9f0bf.png',
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/0edc0785a3e3bf26be7a494886999c4a6f1dc14c.0edc0785.png',
  'https://crow-peanut-06457083.figma.site/_components/v2/4c2b061456bbff22b92923348791b501874ded3f/d12ddf42fe4c8437df4414c883fe60fb77b20cbe.d12ddf42.png',
]

/* ─── Hero 打字机删除文案：CONSTRAIN THE CODE（18 字符） ─── */
const HERO_CHARS = 'CONSTRAIN THE CODE'.split('')
const TOTAL_CHARS = 18

/* ─── Manifesto ─── */
const MANIFESTO = `CodeMason · 编舟

一个能自主完成编码任务的通用 Coding Agent
理解需求 → 规划 → 改码 → 验证 → 交付
全链路自主执行

---

核心能力

1. 自主 Agent Loop
   手写事件流状态机
   Plan/Act 双模式 + 每步 Checkpoint

2. Op/Event 协议
   意图与事实解耦
   加界面不改内核

3. YAGNI 约束引擎
   七级决策阶梯
   写得少，且用四维数据证明

4. 人类在环
   Staging 审查沙盒 + 审批中心
   每一步都看得见

---

技术栈

FastAPI · WebSocket · Vue 3
Tree-sitter · JSONL · Git Checkpoint

为 AI Agent 开发者打造
写得少 · 写得准 · 每一步都看得见`

interface Props {
  onEnterApp: () => void
}

const props = defineProps<Props>()

/* ─── 滚动状态 ─── */
const progress = ref(0)
const trailStickers = ref<Array<{ id: number; x: number; y: number; rot: number; src: string }>>([])

const video1Ref = ref<HTMLVideoElement | null>(null)
const video2Ref = ref<HTMLVideoElement | null>(null)
let rafId = 0
let targetV1 = 0
let targetV2 = 0
let stickerCounter = 0
let lastX = -999
let lastY = -999

/* ─── 派生状态 ─── */
const visibleCount = computed(() => {
  const active = Math.min(progress.value, 0.25) / 0.25
  return Math.round((1 - active) * TOTAL_CHARS)
})

const v1Opacity = computed(() => progress.value < 0.5 ? 1 : 0)
const v2Opacity = computed(() => {
  if (progress.value < 0.45) return 0
  if (progress.value < 0.5) return (progress.value - 0.45) / 0.05
  return 1
})

const manifestoStyle = computed(() => {
  const maxScroll = document.documentElement.scrollHeight - window.innerHeight
  const startProgress = 0.25 + 200 / Math.max(maxScroll, 1)
  if (progress.value < startProgress) {
    return { opacity: 0, transform: 'translateY(100vh)' }
  }
  const alpha = (progress.value - startProgress) / (1 - startProgress)
  const opacity = Math.min(1, alpha / 0.05)
  const translateY = 100 - alpha * 450
  return { opacity, transform: `translateY(${translateY}vh)` }
})

/* ─── HLS 初始化（动态 import，失败自动降级，不影响页面） ─── */
const setupVideo = (video: HTMLVideoElement, url: string) => {
  if (!video) return
  const fallback = () => {
    video.style.background = 'linear-gradient(160deg,#020617 0%,#0b1e3a 45%,#0f4d2e 100%)'
  }
  import('hls.js')
    .then((mod) => {
      const Hls = mod.default || mod
      if (Hls.isSupported()) {
        const hls = new Hls({ maxBufferLength: 60 })
        hls.loadSource(url)
        hls.attachMedia(video)
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().then(() => video.pause()).catch(() => {})
        })
        hls.on(Hls.Events.ERROR, () => fallback())
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url
        video.addEventListener('loadedmetadata', () => {
          video.play().then(() => video.pause()).catch(() => {})
        })
      } else {
        fallback()
      }
    })
    .catch(() => fallback())
}

/* ─── 滚动处理 ─── */
const onScroll = () => {
  const scrollY = window.scrollY
  const maxScroll = document.documentElement.scrollHeight - window.innerHeight
  const p = maxScroll > 0 ? scrollY / maxScroll : 0
  const newProgress = Math.max(0, Math.min(1, p))
  if (Math.abs(progress.value - newProgress) >= 0.001) {
    progress.value = newProgress
  }

  const v1 = video1Ref.value
  const v2 = video2Ref.value
  if (v1 && v1.duration) {
    targetV1 = scrollY <= 10 ? 0 : Math.min(p / 0.5, 1) * v1.duration
  }
  if (v2 && v2.duration) {
    targetV2 = p >= 0.99 ? v2.duration : Math.max(0, (p - 0.5) / 0.5) * v2.duration
  }
}

/* ─── 鼠标轨迹贴纸 ─── */
const onMouseMove = (e: MouseEvent) => {
  const dx = e.clientX - lastX
  const dy = e.clientY - lastY
  if (Math.sqrt(dx * dx + dy * dy) < 150) return
  lastX = e.clientX
  lastY = e.clientY

  const id = Date.now() + Math.random()
  const rot = Math.random() * 40 - 20
  const src = TRAIL_STICKERS[stickerCounter % 5]
  stickerCounter += 1

  trailStickers.value = [
    ...trailStickers.value.slice(-3),
    { id, x: e.clientX, y: e.clientY, rot, src },
  ]
  setTimeout(() => {
    trailStickers.value = trailStickers.value.filter((s) => s.id !== id)
  }, 2200)
}

/* ─── 视频 scrub 循环 ─── */
const scrubLoop = () => {
  const v1 = video1Ref.value
  const v2 = video2Ref.value
  if (v1 && !v1.seeking && v1.duration) {
    const lerped = v1.currentTime + (targetV1 - v1.currentTime) * 0.3
    if (Math.abs(lerped - v1.currentTime) > 0.01) v1.currentTime = lerped
  }
  if (v2 && !v2.seeking && v2.duration) {
    const lerped = v2.currentTime + (targetV2 - v2.currentTime) * 0.3
    if (Math.abs(lerped - v2.currentTime) > 0.01) v2.currentTime = lerped
  }
  rafId = requestAnimationFrame(scrubLoop)
}

/* ─── 生命周期 ─── */
onMounted(() => {
  if (video1Ref.value) setupVideo(video1Ref.value, VIDEO_URL_1)
  if (video2Ref.value) setupVideo(video2Ref.value, VIDEO_URL_2)

  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('mousemove', onMouseMove, { passive: true })
  rafId = requestAnimationFrame(scrubLoop)
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('mousemove', onMouseMove)
  cancelAnimationFrame(rafId)
})

const scrollToBottom = () => {
  document.documentElement.scrollTo({
    top: document.documentElement.scrollHeight,
    behavior: 'smooth',
  })
}

/* ─── 计算属性：英雄文本字符数组 ─── */
const heroChars = computed(() => {
  return HERO_CHARS.map((char, idx) => ({
    char,
    index: idx,
    visible: idx + 1 <= visibleCount.value
  }))
})

const heroLine1 = computed(() => heroChars.value.slice(0, 9))
const heroLine2 = computed(() => heroChars.value.slice(9, 13))
const heroLine3 = computed(() => heroChars.value.slice(13, 18))
</script>

<template>
  <div class="landing-root" data-testid="landing-root">
    <!-- 1. 全屏视频背景 -->
    <div class="video-layer">
      <video
        ref="video1Ref"
        class="video-el"
        :style="{ opacity: v1Opacity }"
        muted
        playsinline
        preload="auto"
        crossorigin="anonymous"
        data-testid="video-1"
      />
      <video
        ref="video2Ref"
        class="video-el"
        :style="{ opacity: v2Opacity }"
        muted
        playsinline
        preload="auto"
        crossorigin="anonymous"
        data-testid="video-2"
      />
    </div>

    <!-- 2. 对角线跑马灯 -->
    <div class="marquee-banner">
      <div class="marquee-track">
        <span class="marquee-text">
          WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING!&nbsp;
        </span>
        <span class="marquee-text">
          WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING!&nbsp;
        </span>
      </div>
    </div>

    <!-- 3. 左上贴纸 -->
    <div class="sticker-fixed" style="top: 232px; left: 120px; transform: rotate(32deg)">
      <img :src="STICKER1" class="sticker-img-100" referrerpolicy="no-referrer" alt="" />
    </div>

    <!-- 4. 右下贴纸 -->
    <div class="sticker-fixed" style="bottom: 32px; right: 32px">
      <img :src="STICKER2" class="sticker-img-150" referrerpolicy="no-referrer" alt="" />
    </div>

    <!-- 5. 导航 -->
    <nav class="nav-bar" data-testid="nav">
      <a class="nav-link" href="#" @click.prevent="props.onEnterApp">
        Start
      </a>
      <a class="nav-link" href="#" @click.prevent="scrollToBottom">
        About
      </a>
      <a class="nav-link" href="#" @click.prevent="scrollToBottom">
        Contact
      </a>
    </nav>

    <!-- 6. Hero 打字机删除文字 -->
    <div class="hero-wrap">
      <h1 class="hero-title" data-testid="hero-title">
        <!-- 第一行：CONSTRA -->
        <div class="hero-line">
          <span
            v-for="item in heroLine1"
            :key="item.index"
            class="hero-char"
            :style="{ opacity: item.visible ? 1 : 0 }"
          >
            {{ item.char }}
          </span>
        </div>
        
        <!-- 第二行：IN THE（可能有贴纸） -->
        <div v-if="visibleCount >= 10" class="hero-line hero-line2">
          <span
            v-for="item in heroLine2"
            :key="item.index"
            class="hero-char"
            :style="{ opacity: item.visible ? 1 : 0 }"
          >
            {{ item.char }}
          </span>
          <img
            v-if="visibleCount >= 13"
            :src="STICKER4"
            class="hero-sticker"
            style="transform: rotate(-8deg)"
            alt=""
          />
          <img
            v-if="visibleCount >= 14"
            :src="STICKER3"
            class="hero-sticker"
            style="transform: rotate(6deg)"
            alt=""
          />
        </div>
        
        <!-- 第三行：CODE -->
        <div v-if="visibleCount >= 15" class="hero-line">
          <span
            v-for="item in heroLine3"
            :key="item.index"
            class="hero-char"
            :style="{ opacity: item.visible ? 1 : 0 }"
          >
            {{ item.char }}
          </span>
        </div>
        
        <span v-if="visibleCount > 0" class="hero-cursor" />
      </h1>
    </div>

    <!-- 7. Manifesto 滚动字幕 -->
    <div class="manifesto-wrap" :style="manifestoStyle" data-testid="manifesto">
      <div class="manifesto-text">{{ MANIFESTO }}</div>
    </div>

    <!-- 8. 鼠标轨迹贴纸 -->
    <div class="trail-layer">
      <div
        v-for="s in trailStickers"
        :key="s.id"
        class="trail-sticker"
        :style="{
          left: `${s.x}px`,
          top: `${s.y}px`,
          transform: `translate(-50%, -50%) rotate(${s.rot}deg)`,
        }"
      >
        <img :src="s.src" class="trail-img" referrerpolicy="no-referrer" alt="" />
      </div>
    </div>

    <!-- 9. 进入驾驶舱（一键直达，无表单障碍） -->
    <div class="enter-app" data-testid="enter-app">
      <button
        class="enter-app-btn"
        data-testid="enter-app-btn"
        @click="props.onEnterApp"
      >
        进入驾驶舱
      </button>
      <p class="enter-app-hint">发任务 · 看每一步 · 关键点审批 —— 一切可回滚可审计</p>
    </div>
  </div>
</template>
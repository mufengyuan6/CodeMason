import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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

FastAPI · WebSocket · React 18
Tree-sitter · JSONL · Git Checkpoint

为 AI Agent 开发者打造
写得少 · 写得准 · 每一步都看得见`

export default function LandingPage({ onEnterApp }) {
  /* ─── 滚动状态 ─── */
  const [progress, setProgress] = useState(0)
  const [formVisible, setFormVisible] = useState(false)
  const [formSubmitted, setFormSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [formData, setFormData] = useState({ name: '', email: '', message: '' })
  const [trailStickers, setTrailStickers] = useState([])

  const video1Ref = useRef(null)
  const video2Ref = useRef(null)
  const rafIdRef = useRef(0)
  const targetV1Ref = useRef(0)
  const targetV2Ref = useRef(0)
  const stickerCounterRef = useRef(0)
  const lastXRef = useRef(-999)
  const lastYRef = useRef(-999)

  /* ─── 派生状态 ─── */
  const visibleCount = useMemo(() => {
    const active = Math.min(progress, 0.25) / 0.25
    return Math.round((1 - active) * TOTAL_CHARS)
  }, [progress])

  const v1Opacity = progress < 0.5 ? 1 : 0
  const v2Opacity = useMemo(() => {
    if (progress < 0.45) return 0
    if (progress < 0.5) return (progress - 0.45) / 0.05
    return 1
  }, [progress])

  const manifestoStyle = useMemo(() => {
    const maxScroll = document.documentElement.scrollHeight - window.innerHeight
    const startProgress = 0.25 + 200 / Math.max(maxScroll, 1)
    if (progress < startProgress) {
      return { opacity: 0, transform: 'translateY(100vh)' }
    }
    const alpha = (progress - startProgress) / (1 - startProgress)
    const opacity = Math.min(1, alpha / 0.05)
    const translateY = 100 - alpha * 450
    return { opacity, transform: `translateY(${translateY}vh)` }
  }, [progress])

  const formStyle = useMemo(() => {
    if (formVisible) {
      return {
        bottom: '50%',
        transform: 'translate(-50%, 50%) rotate(0deg)',
        transitionTimingFunction: 'cubic-bezier(0.16, 1, 0.3, 1)',
      }
    }
    return {
      bottom: '50%',
      transform: 'translate(-50%, 150vh) rotate(15deg)',
      transitionTimingFunction: 'cubic-bezier(0.16, 1, 0.3, 1)',
    }
  }, [formVisible])

  /* ─── HLS 初始化（动态 import，失败自动降级，不影响页面） ─── */
  const setupVideo = useCallback((video, url) => {
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
  }, [])

  /* ─── 滚动处理 ─── */
  const onScroll = useCallback(() => {
    const scrollY = window.scrollY
    const maxScroll = document.documentElement.scrollHeight - window.innerHeight
    const p = maxScroll > 0 ? scrollY / maxScroll : 0
    const newProgress = Math.max(0, Math.min(1, p))
    setProgress((prev) => (Math.abs(prev - newProgress) < 0.001 ? prev : newProgress))

    const v1 = video1Ref.current
    const v2 = video2Ref.current
    if (v1 && v1.duration) {
      targetV1Ref.current =
        scrollY <= 10 ? 0 : Math.min(p / 0.5, 1) * v1.duration
    }
    if (v2 && v2.duration) {
      targetV2Ref.current =
        p >= 0.99 ? v2.duration : Math.max(0, (p - 0.5) / 0.5) * v2.duration
    }
    if (newProgress >= 0.95 && !formVisible) {
      setFormVisible(true)
    }
  }, [formVisible])

  /* ─── 鼠标轨迹贴纸 ─── */
  const onMouseMove = useCallback((e) => {
    const dx = e.clientX - lastXRef.current
    const dy = e.clientY - lastYRef.current
    if (Math.sqrt(dx * dx + dy * dy) < 150) return
    lastXRef.current = e.clientX
    lastYRef.current = e.clientY

    const id = Date.now() + Math.random()
    const rot = Math.random() * 40 - 20
    const src = TRAIL_STICKERS[stickerCounterRef.current % 5]
    stickerCounterRef.current += 1

    setTrailStickers((prev) => [
      ...prev.slice(-3),
      { id, x: e.clientX, y: e.clientY, rot, src },
    ])
    setTimeout(() => {
      setTrailStickers((prev) => prev.filter((s) => s.id !== id))
    }, 2200)
  }, [])

  /* ─── 视频 scrub 循环 ─── */
  const scrubLoop = useCallback(() => {
    const v1 = video1Ref.current
    const v2 = video2Ref.current
    if (v1 && !v1.seeking && v1.duration) {
      const lerped = v1.currentTime + (targetV1Ref.current - v1.currentTime) * 0.3
      if (Math.abs(lerped - v1.currentTime) > 0.01) v1.currentTime = lerped
    }
    if (v2 && !v2.seeking && v2.duration) {
      const lerped = v2.currentTime + (targetV2Ref.current - v2.currentTime) * 0.3
      if (Math.abs(lerped - v2.currentTime) > 0.01) v2.currentTime = lerped
    }
    rafIdRef.current = requestAnimationFrame(scrubLoop)
  }, [])

  /* ─── 生命周期（React 版 onMounted/onUnmounted） ─── */
  useEffect(() => {
    if (video1Ref.current) setupVideo(video1Ref.current, VIDEO_URL_1)
    if (video2Ref.current) setupVideo(video2Ref.current, VIDEO_URL_2)

    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('mousemove', onMouseMove, { passive: true })
    rafIdRef.current = requestAnimationFrame(scrubLoop)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('mousemove', onMouseMove)
      cancelAnimationFrame(rafIdRef.current)
    }
  }, [onScroll, onMouseMove, scrubLoop, setupVideo])

  /* ─── 表单提交 → 进入驾驶舱 ─── */
  const submitForm = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    setSubmitError('')
    try {
      // 进入驾驶舱（WebSocket 事件流 + 审批中心 + YAGNI 看板）
      await new Promise((r) => setTimeout(r, 400))
      onEnterApp()
    } catch (err) {
      setSubmitError(err.message || '进入驾驶舱失败')
    } finally {
      setSubmitting(false)
    }
  }

  const resetForm = () => {
    setFormSubmitted(false)
    setFormData({ name: '', email: '', message: '' })
  }

  const scrollToBottom = () => {
    document.documentElement.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: 'smooth',
    })
  }

  /* ─── 渲染 Hero 三行（逐字 opacity 控制） ─── */
  const renderHeroLine = (startIdx, endIdx, prefix = '') => (
    <span className="hero-line">
      {Array.from({ length: endIdx - startIdx + 1 }, (_, k) => {
        const idx = startIdx + k
        return (
          <span
            key={idx}
            className="hero-char"
            style={{ opacity: idx + 1 <= visibleCount ? 1 : 0 }}
          >
            {HERO_CHARS[idx]}
          </span>
        )
      })}
      {prefix === 'sticker' && (
        <>
          {visibleCount >= 13 && (
            <img
              src={STICKER4}
              className="hero-sticker"
              style={{ transform: 'rotate(-8deg)' }}
              alt=""
            />
          )}
          {visibleCount >= 14 && (
            <img
              src={STICKER3}
              className="hero-sticker"
              style={{ transform: 'rotate(6deg)' }}
              alt=""
            />
          )}
        </>
      )}
    </span>
  )

  return (
    <div className="landing-root" data-testid="landing-root">
      {/* 1. 全屏视频背景 */}
      <div className="video-layer">
        <video
          ref={video1Ref}
          className="video-el"
          style={{ opacity: v1Opacity }}
          muted
          playsInline
          preload="auto"
          crossOrigin="anonymous"
          data-testid="video-1"
        />
        <video
          ref={video2Ref}
          className="video-el"
          style={{ opacity: v2Opacity }}
          muted
          playsInline
          preload="auto"
          crossOrigin="anonymous"
          data-testid="video-2"
        />
      </div>

      {/* 2. 对角线跑马灯 */}
      <div className="marquee-banner">
        <div className="marquee-track">
          <span className="marquee-text">
            WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING!&nbsp;
          </span>
          <span className="marquee-text">
            WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING! WARNING!&nbsp;
          </span>
        </div>
      </div>

      {/* 3. 左上贴纸 */}
      <div className="sticker-fixed" style={{ top: '232px', left: '120px', transform: 'rotate(32deg)' }}>
        <img src={STICKER1} className="sticker-img-100" referrerPolicy="no-referrer" alt="" />
      </div>

      {/* 4. 右下贴纸 */}
      <div className="sticker-fixed" style={{ bottom: '32px', right: '32px' }}>
        <img src={STICKER2} className="sticker-img-150" referrerPolicy="no-referrer" alt="" />
      </div>

      {/* 5. 导航 */}
      <nav className="nav-bar" data-testid="nav">
        <a className="nav-link" href="#" onClick={(e) => { e.preventDefault(); onEnterApp() }}>
          Start
        </a>
        <a className="nav-link" href="#" onClick={(e) => { e.preventDefault(); scrollToBottom() }}>
          About
        </a>
        <a className="nav-link" href="#" onClick={(e) => { e.preventDefault(); scrollToBottom() }}>
          Contact
        </a>
      </nav>

      {/* 6. Hero 打字机删除文字 */}
      <div className="hero-wrap">
        <h1 className="hero-title" data-testid="hero-title">
          <div>{renderHeroLine(0, 8)}</div>
          {visibleCount >= 10 && (
            <div className="hero-line2">
              {renderHeroLine(9, 12, 'sticker')}
            </div>
          )}
          {visibleCount >= 15 && <div>{renderHeroLine(13, 17)}</div>}
          {visibleCount > 0 && <span className="hero-cursor" />}
        </h1>
      </div>

      {/* 7. Manifesto 滚动字幕 */}
      <div className="manifesto-wrap" style={manifestoStyle} data-testid="manifesto">
        <div className="manifesto-text">{MANIFESTO}</div>
      </div>

      {/* 8. 鼠标轨迹贴纸 */}
      <div className="trail-layer">
        {trailStickers.map((s) => (
          <div
            key={s.id}
            className="trail-sticker"
            style={{
              left: `${s.x}px`,
              top: `${s.y}px`,
              transform: `translate(-50%, -50%) rotate(${s.rot}deg)`,
            }}
          >
            <img src={s.src} className="trail-img" referrerPolicy="no-referrer" alt="" />
          </div>
        ))}
      </div>

      {/* 9. 反馈表单（接真实 /analyze API） */}
      <div className="form-box" style={formStyle} data-testid="feedback-form">
        <button className="form-close" onClick={() => setFormVisible(false)}>
          [X]
        </button>

        {!formSubmitted ? (
          <form className="form-inner" onSubmit={submitForm}>
            <h2 className="form-title">进入驾驶舱</h2>
            <input
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              type="text"
              required
              placeholder="输入会话名称"
              className="form-input"
              data-testid="form-name"
            />
            <input
              value={formData.email}
              onChange={(e) => setFormData({ ...formData, email: e.target.value })}
              type="email"
              required
              placeholder="你的邮箱（用于会话备注）"
              className="form-input"
              data-testid="form-email"
            />
            <textarea
              value={formData.message}
              onChange={(e) => setFormData({ ...formData, message: e.target.value })}
              required
              rows={3}
              placeholder="描述第一个任务：让 Agent 帮你改代码、修 bug、写功能…"
              className="form-input form-textarea"
              data-testid="form-message"
            />
            {submitError && <div className="form-error" data-testid="form-error">{submitError}</div>}
            <button
              type="submit"
              className="form-submit"
              disabled={submitting}
              data-testid="form-submit"
            >
              {submitting ? '连接中...' : '进入驾驶舱'}
            </button>
          </form>
        ) : (
          <div className="form-success" data-testid="form-success">
            <div className="success-star">✦</div>
            <h3 className="success-title">优化请求已提交</h3>
            <p className="success-desc">
              驾驶舱连接中：事件流时间线 / 审批中心 / YAGNI 看板已就绪。
            </p>
            <button className="success-btn" onClick={resetForm}>
              [ NEW TRANSMISSION ]
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
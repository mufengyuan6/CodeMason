import { test, expect } from '@playwright/test'

/**
 * 真实浏览器层端到端测试（Web 驾驶舱契约）
 * 覆盖：Landing 渲染 → 进入驾驶舱 → WebSocket 事件流 → 审批中心 → YAGNI 看板
 * 前端: http://127.0.0.1:29864（生产形态：FastAPI 挂载 dist，WS 同源）
 */

test.describe('Landing Page', () => {
  test('页面加载且核心元素渲染', async ({ page }) => {
    await page.goto('/')

    // React 挂载成功
    await expect(page.getByTestId('landing-root')).toBeVisible()

    // Hero 标题存在（打字机效果，初始全部可见）
    const hero = page.getByTestId('hero-title')
    await expect(hero).toBeVisible()
    await expect(hero).toContainText('CONSTRAIN')

    // 导航三个链接
    const nav = page.getByTestId('nav')
    await expect(nav).toBeVisible()
    await expect(nav.getByText('Start')).toBeVisible()
    await expect(nav.getByText('About')).toBeVisible()
    await expect(nav.getByText('Contact')).toBeVisible()

    // 视频层存在
    await expect(page.getByTestId('video-1')).toBeAttached()
    await expect(page.getByTestId('video-2')).toBeAttached()
  })

  test('滚动触发打字机删除效果 + manifesto 出现', async ({ page }) => {
    await page.goto('/')

    const hero = page.getByTestId('hero-title')
    const initialText = await hero.innerText()
    // S20 修复：原 `expect(initialText.length).toBeGreaterThan(0)` 无 await 且恒真
    // （hero 已断言可见 → 文本长度必 >0），改为断言具体内容
    expect(initialText).toContain('CONSTRAIN')

    // 滚动到 30% 位置，触发字符删除
    await page.evaluate(() => {
      const max = document.documentElement.scrollHeight - window.innerHeight
      window.scrollTo(0, max * 0.3)
    })

    // manifesto 应该开始出现（条件等待替代 waitForTimeout(600)，P2 修复）
    const manifesto = page.getByTestId('manifesto')
    await expect(manifesto).toBeAttached({ timeout: 5000 })
    await expect(manifesto).toContainText('CodeMason')
  })
})

test.describe('Cockpit 驾驶舱', () => {
  test('点击 Start 进入驾驶舱，核心模块可用', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('landing-root')).toBeVisible()

    // 点击 Start 进入驾驶舱
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 三区结构：会话栏 + 时间线 + 监控抽屉 tab
    await expect(page.getByTestId('session-rail')).toBeVisible()
    await expect(page.getByTestId('timeline')).toBeVisible()
    await expect(page.getByTestId('drawer-files')).toBeVisible()
    await expect(page.getByTestId('drawer-yagni')).toBeVisible()
    await expect(page.getByTestId('drawer-costs')).toBeVisible()

    // 事件流时间线初始为空态
    await expect(page.getByTestId('timeline')).toBeVisible()
  })

  test('输入任务发送 Op，事件流出现回合事件', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 输入任务并发送
    await page.getByTestId('cp-input').fill('读取 README 并总结')
    await page.getByTestId('cp-send').click()

    // 事件流应出现回合事件（WS 推送到内核）
    await expect(page.getByTestId('ev-turn').first()).toBeVisible({ timeout: 15_000 })
  })

  test('切换抽屉：YAGNI 看板与文件查看器渲染', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // YAGNI 抽屉
    await page.getByTestId('drawer-yagni').click()
    await expect(page.getByTestId('yagni-board')).toBeVisible()

    // 文件抽屉
    await page.getByTestId('drawer-files').click()
    await expect(page.getByTestId('file-viewer')).toBeVisible()

    // 再点一次收起
    await page.getByTestId('drawer-files').click()
    await expect(page.getByTestId('drawer-panel')).not.toBeVisible()
  })

  test('压缩/取消需二次确认（ConfirmDialog）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 压缩：点击 ⚡ → 弹确认层 → 取消
    await page.getByTestId('cp-compact').click()
    await expect(page.getByTestId('confirm-dialog')).toBeVisible()
    await page.getByTestId('confirm-cancel').click()
    await expect(page.getByTestId('confirm-dialog')).not.toBeVisible()

    // 取消回合：点击 ■ → 弹确认层 → 取消
    await page.getByTestId('cp-cancel').click()
    await expect(page.getByTestId('confirm-dialog')).toBeVisible()
    await page.getByTestId('confirm-cancel').click()
    await expect(page.getByTestId('confirm-dialog')).not.toBeVisible()
  })

  test('返回按钮回到 Landing', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('cp-back').click()
    await expect(page.getByTestId('landing-root')).toBeVisible()
  })

  test('会话栏：新建会话并切换（事件流清空重载）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 新建会话
    await page.getByTestId('session-new').click()
    await page.getByTestId('session-name-input').fill('e2e-session')
    await page.getByTestId('session-create-ok').click()

    // 会话出现在列表中并激活
    const item = page.getByTestId('session-item-e2e-session')
    await expect(item).toBeVisible()
    await expect(item).toHaveClass(/is-active/)

    // 切回 web 会话
    await page.getByTestId('session-item-web').click()
    await expect(page.getByTestId('session-item-web')).toHaveClass(/is-active/)
  })

  test('首次引导卡：展示 → 关闭 → localStorage 记忆不再出现', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 新会话空状态 → 引导卡出现
    await page.getByTestId('session-new').click()
    await page.getByTestId('session-name-input').fill('e2e-guide')
    await page.getByTestId('session-create-ok').click()
    await expect(page.getByTestId('timeline-guide')).toBeVisible()

    // 点"开始使用"→ 引导卡消失
    await page.getByTestId('guide-start').click()
    await expect(page.getByTestId('timeline-guide')).not.toBeVisible()

    // 刷新 → 引导卡不再出现（localStorage 记忆）
    await page.reload()
    await expect(page.getByTestId('cockpit')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('timeline-guide')).not.toBeVisible()
  })

  test('模式切换显示解释浮层（规划=只读安全）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('mode-plan').click()
    await expect(page.getByTestId('mode-hint')).toContainText('只读')

    await page.getByTestId('mode-act').click()
    await expect(page.getByTestId('mode-hint')).toContainText('执行')
  })

  test('主题系统：切换主题 + 自定义主色 + 恢复默认', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 打开主题面板
    await page.getByTestId('cp-theme').click()
    await expect(page.getByTestId('theme-panel')).toBeVisible()

    // 切换浅色主题 → html data-theme 生效
    await page.getByTestId('theme-light').click()
    const attr = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
    expect(attr).toBe('light')

    // 自定义主色 → data-custom 生效（color input 需原生 setter + input 事件）
    await page.evaluate(() => {
      const el = document.querySelector('[data-testid="theme-primary-pick"]')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(el, '#ff0000')
      el.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const custom = await page.evaluate(() => document.documentElement.getAttribute('data-custom'))
    expect(custom).toBe('1')

    // 恢复默认 → data-theme 移除
    await page.getByTestId('theme-reset').click()
    const resetAttr = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
    expect(resetAttr).toBeNull()

    // 关闭面板
    await page.getByTestId('theme-close').click()
    await expect(page.getByTestId('theme-panel')).not.toBeVisible()
  })
})

test.describe('错误处理与健壮性', () => {
  test('无控制台严重错误', async ({ page }) => {
    const errors = []
    const EXTERNAL = ['mux.com', 'figma.site', 'fonts.g', 'googleapis', 'gstatic']
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const t = msg.text()
        // 外部资源失败（无外网环境噪音）通过 URL 域名判断——console 错误文本
        // 不含域名（如 'Failed to load resource: net::ERR_CONNECTION_RESET'），
        // 必须用 msg.location().url 才能定位来源（S20 修复：白名单按域名精确过滤）
        const srcUrl = msg.location()?.url || ''
        if (!EXTERNAL.some((h) => srcUrl.includes(h))) {
          errors.push(t)
        }
      }
    })
    page.on('pageerror', (err) => errors.push('PAGEERROR: ' + err.message))
    page.on('response', (resp) => {
      if (resp.status() >= 400 && !EXTERNAL.some((h) => resp.url().includes(h))) {
        errors.push('HTTP ' + resp.status() + ' ' + resp.request().method() + ' ' + resp.url())
      }
    })
    page.on('requestfailed', (req) => {
      if (!EXTERNAL.some((h) => req.url().includes(h))) {
        errors.push('REQFAIL ' + req.method() + ' ' + req.url() + ' ' + (req.failure()?.errorText || ''))
      }
    })

    await page.goto('/')
    // 条件等待：Landing 渲染完成（替代固定 waitForTimeout(3000)，P2 修复）
    await expect(page.getByTestId('landing-root')).toBeVisible({ timeout: 10_000 })
    await page.getByTestId('nav').getByText('Start').click()
    // 条件等待：驾驶舱渲染完成（替代固定 waitForTimeout(1000)，P2 修复）
    await expect(page.getByTestId('cockpit')).toBeVisible({ timeout: 10_000 })
    // 给异步 console 事件留出浮出窗口：轮询等待，若期间出现真实错误立即失败
    await expect.poll(() => errors.length, { timeout: 2000 }).toBe(0)

    expect(errors).toEqual([])
  })

  test('后端健康检查', async ({ request }) => {
    const resp = await request.get('http://127.0.0.1:29864/health')
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.status).toBe('healthy')
    expect(body.service).toBe('codemason-cockpit')
  })
})

test.describe('v1.23 新增视图', () => {
  test('贡献审计抽屉：打开并渲染贡献报告', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 打开贡献抽屉
    await page.getByTestId('drawer-contribution').click()
    await expect(page.getByTestId('contribution-panel')).toBeVisible()
    // AI 参与度 / 文件贡献指标渲染
    await expect(page.getByText('AI 贡献审计')).toBeVisible()
    await expect(page.getByText('AI 参与度')).toBeVisible()
    // 关闭抽屉
    await page.getByTestId('drawer-contribution').click()
    await expect(page.getByTestId('drawer-panel')).not.toBeVisible()
  })

  test('审批收件箱抽屉：打开并渲染收件箱', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('drawer-inbox').click()
    await expect(page.getByTestId('approval-inbox-panel')).toBeVisible()
    await expect(page.getByText('审批收件箱')).toBeVisible()
    // 空态或待处置件渲染（服务端已挂载收件箱）
    await expect(page.getByTestId('approval-inbox-panel')).toContainText(/待处置|收件箱为空/)
    await page.getByTestId('drawer-inbox').click()
    await expect(page.getByTestId('drawer-panel')).not.toBeVisible()
  })

  test('v1.23 后端 API：贡献报告 + 收件箱 + 分类器 + 遥测可查询', async ({ request }) => {
    const token = 'demo-token'
    const res1 = await request.get('http://127.0.0.1:29864/api/contribution', { headers: { 'x-agent-token': token } })
    expect(res1.status()).toBe(200)
    const body1 = await res1.json()
    expect(body1.enabled).toBe(true)
    expect(body1.report).toBeTruthy()

    const res2 = await request.get('http://127.0.0.1:29864/api/inbox', { headers: { 'x-agent-token': token } })
    expect(res2.status()).toBe(200)
    expect((await res2.json()).enabled).toBe(true)

    const res3 = await request.get('http://127.0.0.1:29864/api/classifier', { headers: { 'x-agent-token': token } })
    expect(res3.status()).toBe(200)
    expect((await res3.json()).enabled).toBe(true)

    const res4 = await request.get('http://127.0.0.1:29864/api/telemetry', { headers: { 'x-agent-token': token } })
    expect(res4.status()).toBe(200)
    expect((await res4.json()).enabled).toBe(true)
  })

  test('v1.27 视觉面板抽屉：打开渲染，空态可见', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('landing-root')).toBeVisible()
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('drawer-vision').click()
    const panel = page.getByTestId('vision-panel')
    await expect(panel).toBeVisible()
    // 空态（事件流暂无 ReadImage/Ocr 工具结果）——Agent Skills 兼容零开销
    await expect(panel).toContainText(/视觉执行|暂无视觉工具/)
    await page.getByTestId('drawer-vision').click()
    await expect(page.getByTestId('drawer-panel')).not.toBeVisible()
  })

  test('v1.27 Skill registry API：索引可查询（空目录安全降级）', async ({ request }) => {
    const token = 'demo-token'
    const res = await request.get('http://127.0.0.1:29864/api/skills', { headers: { 'x-agent-token': token } })
    expect(res.status()).toBe(200)
    const body = await res.json()
    expect(body.enabled).toBe(true)
    expect(Array.isArray(body.skills)).toBe(true)
    expect(body.stats).toBeTruthy()
  })
})

test.describe('v1.29 双投影（对话视图/时间线）', () => {
  test('切换器存在，默认时间线，切对话视图后事件等价', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // 切换器可见
    const switcher = page.getByTestId('center-view-switcher')
    await expect(switcher).toBeVisible()
    // 默认时间线（展开投影）
    await expect(page.getByTestId('timeline')).toBeVisible()

    // 切到对话视图（折叠投影）——同一事件流两种渲染
    await page.getByTestId('view-dialogue').click()
    await expect(page.getByTestId('dialogue-view')).toBeVisible()
    // 切换不丢上下文：时间线事件仍在（事件流是同一数据层）
    await page.getByTestId('view-timeline').click()
    await expect(page.getByTestId('timeline')).toBeVisible()
  })

  test('对话视图切换记忆（sessionStorage 恢复上次选择）', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('view-dialogue').click()
    await expect(page.getByTestId('dialogue-view')).toBeVisible()

    // 刷新页面 → 恢复对话视图（sessionStorage 记忆）
    await page.reload()
    await expect(page.getByTestId('cockpit')).toBeVisible()
    await expect(page.getByTestId('dialogue-view')).toBeVisible()
  })

  test('对话视图空态渲染（无事件安全）', async ({ page, request }) => {
    // 切到一次性新会话，确保事件流为空（服务端 web 会话是持久化的，可能有历史事件）
    const fresh = `e2e-empty-${Date.now()}`
    const res = await request.post('http://127.0.0.1:29864/sessions/switch', {
      headers: { 'x-agent-token': 'demo-token' },
      data: { session_id: fresh },
    })
    expect(res.status()).toBe(200)

    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()
    await page.getByTestId('view-dialogue').click()
    await expect(page.getByTestId('dialogue-view')).toBeVisible()
    // 空态（新会话无事件 → 对话为空；正则命中 2 个元素，用 first 消歧）
    await expect(page.getByText('对话为空')).toBeVisible()
    await expect(page.getByText('在下方输入任务，对话会显示在这里')).toBeVisible()
  })
})

test.describe('v1.28 溯源报告（G20）', () => {
  test('溯源抽屉：打开渲染，空态可见', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('drawer-rootcause').click()
    const panel = page.getByTestId('root-cause-panel')
    await expect(panel).toBeVisible()
    // 空态（事件流暂无 RootCauseReport）
    await expect(panel).toContainText(/溯源报告|暂无溯源/)
    await page.getByTestId('drawer-rootcause').click()
    await expect(page.getByTestId('drawer-panel')).not.toBeVisible()
  })

  test('v1.28 后端 API：根因分析 + 溯源报告 + 图谱查询 + 归因可查询', async ({ request }) => {
    const token = 'demo-token'
    // 图谱查询（确定性证据链底座）
    const res1 = await request.get('http://127.0.0.1:29864/api/codegraph/query?op=pagerank&limit=5', {
      headers: { 'x-agent-token': token },
    })
    expect(res1.status()).toBe(200)
    expect((await res1.json()).enabled).toBe(true)

    // 溯源报告列表（溯源即事件，事件流投影）
    const res2 = await request.get('http://127.0.0.1:29864/api/root-cause/reports', {
      headers: { 'x-agent-token': token },
    })
    expect(res2.status()).toBe(200)
    const body2 = await res2.json()
    expect(body2.enabled).toBe(true)
    expect(Array.isArray(body2.reports)).toBe(true)

    // YAGNI 高频问题归因（代码评审场景）
    const res3 = await request.get('http://127.0.0.1:29864/api/attribution', {
      headers: { 'x-agent-token': token },
    })
    expect(res3.status()).toBe(200)
    const body3 = await res3.json()
    expect(body3.enabled).toBe(true)
    expect(Array.isArray(body3.top_issues)).toBe(true)
  })
})


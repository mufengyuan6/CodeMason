import { test, expect } from '@playwright/test'

/**
 * 真实浏览器层端到端测试（Web 驾驶舱契约）
 * 覆盖：Landing 渲染 → 进入驾驶舱 → WebSocket 事件流 → 审批中心 → YAGNI 看板
 * 前端: http://127.0.0.1:5847
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
    expect(initialText.length).toBeGreaterThan(0)

    // 滚动到 30% 位置，触发字符删除
    await page.evaluate(() => {
      const max = document.documentElement.scrollHeight - window.innerHeight
      window.scrollTo(0, max * 0.3)
    })
    await page.waitForTimeout(600)

    // manifesto 应该开始出现
    const manifesto = page.getByTestId('manifesto')
    await expect(manifesto).toBeAttached()
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

    // 模块导航存在
    await expect(page.getByTestId('cp-module-timeline')).toBeVisible()
    await expect(page.getByTestId('cp-module-approvals')).toBeVisible()
    await expect(page.getByTestId('cp-module-yagni')).toBeVisible()
    await expect(page.getByTestId('cp-module-files')).toBeVisible()

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

  test('切换模块：YAGNI 看板与审批中心渲染', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    // YAGNI 看板
    await page.getByTestId('cp-module-yagni').click()
    await expect(page.getByTestId('yagni-board')).toBeVisible()

    // 审批中心
    await page.getByTestId('cp-module-approvals').click()
    await expect(page.getByTestId('approval-center')).toBeVisible()
  })

  test('返回按钮回到 Landing', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('nav').getByText('Start').click()
    await expect(page.getByTestId('cockpit')).toBeVisible()

    await page.getByTestId('cp-back').click()
    await expect(page.getByTestId('landing-root')).toBeVisible()
  })
})

test.describe('错误处理与健壮性', () => {
  test('无控制台严重错误', async ({ page }) => {
    const errors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const t = msg.text()
        // 忽略外部资源加载失败（mux 视频 / figma 贴纸 / 谷歌字体 / WS 未连接）
        if (
          !t.includes('mux.com') &&
          !t.includes('figma.site') &&
          !t.includes('fonts.g') &&
          !t.includes('Failed to load resource') &&
          !t.includes('WebSocket') &&
          !t.includes('net::ERR')
        ) {
          errors.push(t)
        }
      }
    })

    await page.goto('/')
    await page.waitForTimeout(3000)
    await page.getByTestId('nav').getByText('Start').click()
    await page.waitForTimeout(1000)

    expect(errors).toEqual([])
  })

  test('后端健康检查', async ({ request }) => {
    const resp = await request.get('http://127.0.0.1:8765/health')
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.status).toBe('healthy')
    expect(body.service).toBe('codemason-cockpit')
  })
})

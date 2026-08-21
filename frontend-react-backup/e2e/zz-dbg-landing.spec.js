import { test } from '@playwright/test'

test('debug landing animation', async ({ page }) => {
  const errors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text().slice(0, 120))
  })
  page.on('requestfailed', (req) => errors.push('REQFAIL: ' + req.url().slice(0, 120)))

  await page.goto('/')
  await page.waitForTimeout(6000) // 等 hls 加载

  // 首屏状态
  const state = await page.evaluate(() => {
    const v1 = document.querySelector('[data-testid="video-1"]')
    const v2 = document.querySelector('[data-testid="video-2"]')
    const hero = document.querySelector('[data-testid="hero-title"]')
    return {
      v1Opacity: v1 ? getComputedStyle(v1).opacity : 'no-el',
      v1ReadyState: v1 ? v1.readyState : -1,
      v1CurrentTime: v1 ? v1.currentTime.toFixed(2) : -1,
      v1Duration: v1 ? (v1.duration || 0).toFixed(2) : -1,
      v1Bg: v1 ? getComputedStyle(v1).backgroundImage.slice(0, 60) : '',
      heroText: hero ? hero.innerText.slice(0, 40) : '',
      trailCount: document.querySelectorAll('.trail-sticker').length,
    }
  })
  console.log('STATE:', JSON.stringify(state))
  console.log('ERRORS:', JSON.stringify(errors.slice(0, 8)))
  await page.screenshot({ path: 'C:/Users/LENOVO/AppData/Local/Temp/codemason-landing-top.png', fullPage: false })

  // 滚动到 60% 看 manifesto/video2
  await page.evaluate(() => {
    const max = document.documentElement.scrollHeight - window.innerHeight
    window.scrollTo(0, max * 0.6)
  })
  await page.waitForTimeout(1500)
  const state2 = await page.evaluate(() => {
    const v2 = document.querySelector('[data-testid="video-2"]')
    return { v2Opacity: v2 ? getComputedStyle(v2).opacity : 'no-el', v2CurrentTime: v2 ? v2.currentTime.toFixed(2) : -1, v2Duration: v2 ? (v2.duration || 0).toFixed(2) : -1 }
  })
  console.log('STATE2:', JSON.stringify(state2))
  await page.screenshot({ path: 'C:/Users/LENOVO/AppData/Local/Temp/codemason-landing-scroll.png', fullPage: false })
})

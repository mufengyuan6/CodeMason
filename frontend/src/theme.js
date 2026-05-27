/**
 * 主题系统（v1.16 · 换壁纸式个性化）
 * - 内置 3 套：jade-gold（墨绿金，默认）/ obsidian（深色中性）/ light（浅色）
 * - 自定义主色/强调色：覆盖 --c-primary/--c-accent，soft 变体自动派生
 * - localStorage 持久化
 */

export const THEMES = [
  { id: 'jade-gold', name: '墨绿金', desc: '默认 · 暗色高级', swatch: ['#0B1A14', '#10B981', '#EAB308'] },
  { id: 'obsidian', name: '深色中性', desc: '青黄 · VS Code 感', swatch: ['#16181D', '#22D3EE', '#FBBF24'] },
  { id: 'light', name: '浅色', desc: '翡翠绿 · 琥珀', swatch: ['#F7F8FA', '#059669', '#D97706'] },
]

const THEME_KEY = 'codemason-theme'

/** hex → 变体（lighten=true 向白混合，否则向黑混合），用于派生 soft 色 */
function shade(hex, amount) {
  const n = parseInt(hex.replace('#', ''), 16)
  const r = (n >> 16) & 0xff
  const g = (n >> 8) & 0xff
  const b = n & 0xff
  const mix = (c) => Math.round(c + (c < 128 ? amount : -amount))
  const to = (c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, '0')
  return `#${to(mix(r))}${to(mix(g))}${to(mix(b))}`
}

/** 应用主题到 <html>（渲染前调用，防闪烁） */
export function applyTheme({ id = 'jade-gold', primary = null, accent = null } = {}) {
  const root = document.documentElement
  if (id === 'jade-gold') {
    root.removeAttribute('data-theme')
  } else {
    root.setAttribute('data-theme', id)
  }
  if (primary || accent) {
    // 支持只自定义一个色：缺省侧沿用当前主题色（读 CSS 变量，不受影响）
    const cur = getComputedStyle(root)
    const p = primary || cur.getPropertyValue('--c-primary').trim() || '#10B981'
    const a = accent || cur.getPropertyValue('--c-accent').trim() || '#EAB308'
    root.setAttribute('data-custom', '1')
    root.style.setProperty('--custom-primary', p)
    root.style.setProperty('--custom-primary-soft', shade(p, 22))
    root.style.setProperty('--custom-accent', a)
    root.style.setProperty('--custom-accent-soft', shade(a, 22))
  } else {
    root.removeAttribute('data-custom')
    for (const p of ['--custom-primary', '--custom-primary-soft', '--custom-accent', '--custom-accent-soft']) {
      root.style.removeProperty(p)
    }
  }
  try {
    localStorage.setItem(THEME_KEY, JSON.stringify({ id, primary, accent }))
  } catch {
    /* ignore */
  }
}

/** 挂载时恢复主题（返回已保存配置） */
export function loadTheme() {
  try {
    const saved = JSON.parse(localStorage.getItem(THEME_KEY) || 'null')
    if (saved) applyTheme(saved)
    return saved || { id: 'jade-gold', primary: null, accent: null }
  } catch {
    return { id: 'jade-gold', primary: null, accent: null }
  }
}

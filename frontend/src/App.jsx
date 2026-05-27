import { useCallback, useState } from 'react'
import LandingPage from './components/LandingPage.jsx'
import Cockpit from './components/Cockpit.jsx'
import { loadTheme } from './theme.js'

// 渲染前恢复主题（防闪烁：data-theme 先于 React 挂载设置）
loadTheme()

/**
 * 顶层 App：landing 视图 ⇄ 驾驶舱视图 切换
 * - 视图状态 sessionStorage 持久化：刷新/重开回到驾驶舱（会话恢复，蔡格尼克）
 * - 进入驾驶舱时记录，返回落地页时清除
 */
export default function App() {
  const [view, setView] = useState(() => {
    try {
      return sessionStorage.getItem('codemason-view') === 'cockpit' ? 'cockpit' : 'landing'
    } catch {
      return 'landing'
    }
  })

  const enterApp = useCallback(() => {
    try {
      sessionStorage.setItem('codemason-view', 'cockpit')
    } catch {
      /* ignore */
    }
    setView('cockpit')
  }, [])

  const backToLanding = useCallback(() => {
    try {
      sessionStorage.removeItem('codemason-view')
    } catch {
      /* ignore */
    }
    setView('landing')
  }, [])

  if (view === 'cockpit') {
    return <Cockpit onBack={backToLanding} />
  }

  return <LandingPage onEnterApp={enterApp} />
}

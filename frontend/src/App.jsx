import { useState } from 'react'
import LandingPage from './components/LandingPage.jsx'
import Cockpit from './components/Cockpit.jsx'

/**
 * 顶层 App：landing 视图 ⇄ 驾驶舱视图 切换
 */
export default function App() {
  const [view, setView] = useState('landing')

  if (view === 'cockpit') {
    return <Cockpit onBack={() => setView('landing')} />
  }

  return <LandingPage onEnterApp={() => setView('cockpit')} />
}

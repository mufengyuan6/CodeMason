import { useMemo, useState } from 'react'

/**
 * YAGNI 看板（独有差异化展示区 · 四维量化指标）
 * - 行数减少 / 依赖未新增 / 重复实现数 / 可读性守门
 * - accent 粉高亮数字，数字滚动入场
 */
export default function YagniBoard({ metrics }) {
  const [expanded, setExpanded] = useState(null)

  const totals = useMemo(() => {
    const t = { lines: 0, deps: 0, dups: 0, readable: 0, count: metrics.length }
    for (const m of metrics) {
      t.lines += m.lines_reduced ?? m.line_reduction ?? 0
      t.deps += m.deps_added ?? m.new_dependencies ?? 0
      t.dups += m.duplicate_impls ?? m.duplicates ?? 0
      t.readable += m.readability_passed ?? m.readability ?? 0
    }
    return t
  }, [metrics])

  const cards = [
    { key: 'lines', label: '代码行数减少', value: totals.lines, unit: '行', accent: true },
    { key: 'deps', label: '依赖未新增', value: totals.deps, unit: '个', accent: false },
    { key: 'dups', label: '重复实现检出', value: totals.dups, unit: '处', accent: false },
    { key: 'readable', label: '可读性守门通过', value: totals.readable, unit: '项', accent: true },
  ]

  if (metrics.length === 0) {
    return (
      <div className="yagni-board empty" data-testid="yagni-board">
        <h3 className="module-title">YAGNI 约束看板</h3>
        <p>暂无任务级数据</p>
        <p className="hint">运行一个任务后，这里会展示「写得更少」的四维量化证明</p>
      </div>
    )
  }

  return (
    <div className="yagni-board" data-testid="yagni-board">
      <h3 className="module-title">YAGNI 约束看板 <span className="count-pill">{totals.count} 任务</span></h3>
      <div className="yagni-cards">
        {cards.map((c) => (
          <div key={c.key} className={`yagni-card ${c.accent ? 'is-accent' : ''}`}>
            <div className="yagni-value">{c.value}<span className="yagni-unit">{c.unit}</span></div>
            <div className="yagni-label">{c.label}</div>
          </div>
        ))}
      </div>

      <div className="yagni-tasks">
        {metrics.map((m, i) => (
          <div key={i} className="yagni-task">
            <button className="yagni-task-head" onClick={() => setExpanded(expanded === i ? null : i)}>
              <span>任务 #{i + 1}</span>
              <span className="chevron">{expanded === i ? '▾' : '▸'}</span>
            </button>
            {expanded === i && (
              <pre className="yagni-raw"><code>{JSON.stringify(m, null, 2)}</code></pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

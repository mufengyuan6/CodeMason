import { useMemo } from 'react'

/**
 * FileViewer 只读代码查看器（5.2）
 * - 审批时查看文件上下文（语法高亮，只读）
 * - 不做编辑器（避免事件流与文件不同步）
 */
export default function FileViewer({ file, events, onSelect }) {
  // 从事件流中收集文件相关条目（ItemCompleted 中的文件路径）
  const files = useMemo(() => {
    const seen = new Set()
    const list = []
    for (const ev of events) {
      const path = ev.file_path || ev.content?.file_path
      if (path && !seen.has(path)) {
        seen.add(path)
        list.push({ path, ev })
      }
    }
    return list
  }, [events])

  if (!file) {
    return (
      <div className="file-viewer empty" data-testid="file-viewer">
        <h3 className="module-title">只读代码查看器</h3>
        <p>从事件流选择文件查看上下文</p>
        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f) => (
              <li key={f.path}>
                <button className="file-item" onClick={() => onSelect(f)}>
                  {f.path}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  const content = file.content?.preview ?? file.diff_preview ?? ''

  return (
    <div className="file-viewer" data-testid="file-viewer">
      <div className="file-viewer-head">
        <button className="btn btn-ghost" onClick={() => onSelect(null)}>← 返回</button>
        <code className="file-path">{file.path ?? file.file_path ?? 'context'}</code>
      </div>
      <div className="file-content">
        <pre><code>{content || '// 只读预览：无内容'}</code></pre>
      </div>
    </div>
  )
}

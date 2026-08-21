/**
 * 视觉执行面板（v1.27 · 多模态信息通道可视化）
 * - UI-TARS 式"看得见的执行"：图片预览 + ReadImage 描述 + OCR 文字块
 * - 从事件流中提取 ReadImage/Ocr 工具结果（事件溯源即数据源，零新存储）
 * - 严格抽屉化不升 tab（v1.14 约束：主流程不被监控稀释）
 */
import { useMemo } from 'react'

/**
 * 从事件流中提取视觉工具结果（ReadImage / Ocr）。
 * 事件结构：ItemCompleted / ToolResult 事件携带工具名与输出。
 */
function extractVisionEntries(events) {
  if (!Array.isArray(events)) return []
  const out = []
  for (const ev of events) {
    const payload = ev.payload || ev
    const tool = payload.tool || payload.tool_name || ''
    if (tool === 'ReadImage' || tool === 'Ocr') {
      const result = payload.result || payload.output || {}
      out.push({
        id: ev.id || `${tool}-${out.length}`,
        tool,
        path: result.path || payload.path || '',
        description: result.description || '',
        text: result.text || '',
        blocks: Array.isArray(result.blocks) ? result.blocks : [],
        status: result.status || payload.status || 'unknown',
        ts: ev.ts || payload.ts || null,
      })
    }
  }
  return out
}

const TOOL_LABEL = { ReadImage: '视觉子代理', Ocr: 'OCR 读字' }
const STATUS_CLASS = { ok: 'vision-ok', error: 'vision-error', unknown: 'vision-unknown' }

export default function VisionPanel({ events = [] }) {
  const entries = useMemo(() => extractVisionEntries(events), [events])

  return (
    <div className="panel" data-testid="vision-panel">
      <h3 className="panel-title">👁 视觉执行</h3>
      {entries.length === 0 && (
        <div className="panel-empty">暂无视觉工具调用（Agent 调用 ReadImage / Ocr 后这里可见）</div>
      )}
      {entries.map((e) => (
        <div key={e.id} className={`vision-entry ${STATUS_CLASS[e.status] || ''}`} data-testid={`vision-entry-${e.tool}`}>
          <div className="vision-head">
            <span className="vision-tool">{TOOL_LABEL[e.tool] || e.tool}</span>
            {e.path && <span className="vision-path" title={e.path}>{e.path.split(/[\\/]/).pop()}</span>}
            <span className="vision-status">{e.status}</span>
          </div>
          {e.description && (
            <div className="vision-desc" data-testid="vision-description">{e.description}</div>
          )}
          {e.text && (
            <div className="vision-ocr">
              <div className="vision-ocr-label">OCR 文字块（{e.blocks.length} 块）</div>
              <pre className="vision-ocr-text">{e.text}</pre>
            </div>
          )}
          {e.blocks?.length > 0 && (
            <div className="vision-blocks">
              {e.blocks.slice(0, 20).map((b, i) => (
                <div key={i} className="vision-block-row">
                  <span className="vision-block-text">{b.text || ''}</span>
                  {typeof b.confidence === 'number' && (
                    <span className="vision-block-conf">{(b.confidence * 100).toFixed(0)}%</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

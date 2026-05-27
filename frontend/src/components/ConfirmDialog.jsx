/**
 * 确认弹层：不可逆操作（压缩/取消回合）最小化误触
 * - 压缩：可审计可回读提示
 * - 取消：已落盘变更保留提示
 */
export default function ConfirmDialog({ title, desc, confirmLabel, onConfirm, onCancel }) {
  return (
    <div className="confirm-overlay" data-testid="confirm-dialog" onClick={onCancel}>
      <div className="confirm-box" onClick={(e) => e.stopPropagation()}>
        <h3 className="confirm-title">{title}</h3>
        <p className="confirm-desc">{desc}</p>
        <div className="confirm-actions">
          <button className="btn btn-ghost" onClick={onCancel} data-testid="confirm-cancel">
            取消
          </button>
          <button className="btn btn-danger" onClick={onConfirm} data-testid="confirm-ok">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

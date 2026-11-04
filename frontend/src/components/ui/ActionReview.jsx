import { useState } from 'react'
import { callTool } from '../../api/mcpClient'
import { decisionArgs, actionMessage } from '../../utils/approval'

export default function ActionReview({ proposal, onComplete, onClose = () => {} }) {
  const [decisionKey] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const decide = async (approved) => {
    setBusy(true); setError('')
    try {
      const response = await callTool('run_terraform_apply_mcp', {
        ...decisionArgs(proposal, approved, decisionKey + (approved ? '' : '-reject')), aws_region: proposal.aws_identity?.region,
      })
      setResult(response)
      if (response.status === 'complete') onComplete?.(response)
    } catch (error) { setError(`${error.message}. Check history before retrying; the response may have been lost.`) }
    finally { setBusy(false) }
  }
  return (
    <div className="aca-summary" role="region" aria-label="Review proposed action">
      <strong>{proposal.action_type} · {proposal.aws_identity?.account_id} · {proposal.aws_identity?.region}</strong>
      <div className="mono" style={{ overflowWrap: 'anywhere', fontSize: 11 }}>Approval digest: {proposal.approval_digest || 'Unavailable'}</div>
      <pre className="aca-code" style={{ maxHeight: 260, overflow: 'auto' }}>{proposal.plan_output || proposal.error}</pre>
      {result ? <p role="status">{actionMessage(result)}</p> : <div className="aca-row" style={{ gap: 8 }}>
        <button className="aca-btn-primary" disabled={busy || proposal.status !== 'awaiting_approval'} onClick={() => decide(true)}>
          {busy ? 'Submitting…' : 'Approve reviewed action'}
        </button>
        <button className="aca-btn-ghost small" disabled={busy} onClick={() => decide(false)}>Reject</button>
      </div>}
      {error && <p role="alert">{error}</p>}
      <button className="aca-btn-ghost small" disabled={busy} onClick={onClose}>Close review</button>
    </div>
  )
}

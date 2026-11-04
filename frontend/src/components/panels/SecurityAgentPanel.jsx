import ActionReview from '../ui/ActionReview'
import { decisionArgs, actionMessage } from '../../utils/approval'
import ScanCoverage from '../ui/ScanCoverage'
import { verificationMessage } from '../../utils/assessment'
import { useState, useEffect } from 'react'
import { callTool } from '../../api/mcpClient'
import { AGENT_STEPS } from '../../utils/constants'

function PhasePill({ phase }) {
  const map = {
    idle:               ['muted',   'IDLE'],
    thinking:           ['blue',    'THINKING'],
    awaiting_approval:  ['warning', 'AWAITING APPROVAL'],
    applying:           ['blue',    'APPLYING'],
    complete:           ['success', 'COMPLETE'],
    failed:             ['error',   'FAILED'],
    incomplete:         ['warning', 'INCOMPLETE'],
    uncertain:          ['warning', 'OUTCOME UNCERTAIN'],
    invalidated:        ['warning', 'NEW REVIEW REQUIRED'],
    rejected:           ['muted', 'REJECTED'],
    no_issues:          ['muted', 'CHECKS COMPLETED'],
  }
  const [cls, label] = map[phase] || map.idle
  return <span className={'aca-badge ' + cls}>{label}</span>
}

function AgentPhaseSteps({ step, done = false }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {AGENT_STEPS.map((s, i) => {
        const state = done || i < step ? 'past' : i === step ? 'current' : 'future'
        const sym   = state === 'past' ? '✓' : state === 'current' ? '›' : '○'
        const color = state === 'past' ? 'var(--success)' : state === 'current' ? 'var(--accent-blue)' : 'var(--text-muted)'
        return (
          <div key={i} style={{
            display: 'flex', gap: 10, color,
            fontSize: 12, opacity: state === 'future' ? 0.55 : 1,
            transition: 'opacity 0.3s ease, color 0.3s ease',
          }}>
            <span className="mono" style={{ width: 14, textAlign: 'center' }}>{sym}</span>
            <span style={{ color: state === 'future' ? 'var(--text-muted)' : 'var(--text-primary)' }}>{s}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function SecurityAgentPanel({ region, model, onApplyComplete }) {
  const [destroyProposal, setDestroyProposal] = useState(null)
  const [decisionKey] = useState(() => crypto.randomUUID())
  const [phase,        setPhase]        = useState('idle')
  const [step,         setStep]         = useState(0)
  const [data,         setData]         = useState(null)
  const [result,       setResult]       = useState(null)
  const [showHcl,      setShowHcl]      = useState(false)
  const [showPlan,     setShowPlan]     = useState(false)
  const [error,        setError]        = useState(null)
  const [verifying,    setVerifying]    = useState(false)
  const [verifyResult, setVerifyResult] = useState(null)
  const [rollingBack,  setRollingBack]  = useState(false)
  const [rollbackDone, setRollbackDone] = useState(false)
  const [resolved,     setResolved]     = useState(false)

  useEffect(() => {
    if (phase !== 'thinking' && phase !== 'applying') return
    const id = setInterval(() => setStep((s) => Math.min(s + 1, AGENT_STEPS.length - 1)), 1200)
    return () => clearInterval(id)
  }, [phase])

  const run = async () => {
    setPhase('thinking'); setStep(0); setData(null); setResult(null); setError(null)
    try {
      const r = await callTool('agent_run', {
        region, model,
      })
      setData(r)
      if (r.status === 'awaiting_approval') setPhase('awaiting_approval')
      else if (['partial', 'failed', 'no_issues'].includes(r.status)) setPhase(r.status === 'no_issues' ? 'no_issues' : 'incomplete')
      else { setError(r.error || 'Agent did not produce a plan'); setPhase('failed') }
    } catch (e) { setError(String(e)); setPhase('failed') }
  }

  const approve = async () => {
    setPhase('applying'); setStep(0)
    try {
      const r = await callTool('agent_approve', {
        ...decisionArgs(data, true, decisionKey),
        aws_region:            region        || 'us-east-1',
      })
      setResult(r)
      const finalPhase = ['complete', 'applying', 'uncertain', 'invalidated', 'rejected', 'failed'].includes(r.status) ? r.status : 'failed'
      setPhase(finalPhase)
      if (finalPhase === 'complete') onApplyComplete?.()
      else setError(actionMessage(r))
    } catch (e) { setError(`${e.message}. ${actionMessage({ status: 'uncertain' })}`); setPhase('uncertain') }
  }

  const downloadReport = () => {
    const report = {
      generated_at:  new Date().toISOString(),
      execution_id:  data?.execution_id,
      status:        'complete',
      issue:         data?.issue,
      summary:       data?.summary,
      hcl:           data?.hcl,
      plan_output:   data?.plan_output,
      apply_output:  result?.apply_output,
    }
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `audit-${data?.execution_id || 'report'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const markResolved = async () => {
    try {
      await callTool('mark_execution_resolved', { execution_id: data.execution_id })
      setResolved(true)
    } catch { /* best-effort — log survives even if mark fails */ }
  }

  const rollback = async () => {
    setRollingBack(true)
    try {
      const proposal = await callTool('rollback_execution', {
        idempotency_key: crypto.randomUUID(),
        execution_id:          data.execution_id,
        aws_region:            region || 'us-east-1',
      })
      setDestroyProposal(proposal)
    } catch (error) { setError(error.message) }
    finally { setRollingBack(false) }
  }

  const verify = async () => {
    setVerifying(true)
    setVerifyResult(null)
    try {
      const r = await callTool('run_security_analysis_with_summary', { model, region: region || 'us-east-1' })
      setVerifyResult(r)
    } catch { setVerifyResult({ status: 'failed' }) }
    finally { setVerifying(false) }
  }

  const reject = async () => {
    try {
      const result = await callTool('agent_approve', {
        ...decisionArgs(data, false, decisionKey + '-reject'), aws_region: region || 'us-east-1',
      })
      setPhase(result.status)
      setError(actionMessage(result))
    } catch (error) { setError(error.message) }
  }

  return (
    <section className="aca-panel" style={{ gridColumn: 'span 12' }}>
      <div className="aca-panel-hd">
        <div className="aca-row" style={{ gap: 10 }}>
          <span className="aca-panel-title">Security Agent</span>
          <PhasePill phase={phase} />
        </div>
      </div>
      <div className="aca-panel-body">
        {destroyProposal && <ActionReview key={destroyProposal.execution_id} proposal={destroyProposal} onClose={() => setDestroyProposal(null)} onComplete={() => { setRollbackDone(true); onApplyComplete?.() }} />}
        {data?.approval_digest && phase === 'awaiting_approval' && <div className="aca-summary">
          {data.action_type} · AWS {data.aws_identity?.account_id} · {data.aws_identity?.region}
          <div className="mono" style={{ overflowWrap: 'anywhere' }}>Approval digest: {data.approval_digest}</div>
        </div>}
        {['incomplete', 'no_issues'].includes(phase) && (
          <div>
            <ScanCoverage data={data} />
            <p>{data?.message || data?.llm_summary}</p>
            <button className="aca-btn-primary" onClick={run}>Run Agent Again</button>
          </div>
        )}
        {phase === 'idle' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.55, maxWidth: 640 }}>
              The agent runs a full scan, picks the highest-severity finding, and generates a fix —
              direct AWS API for open port rules, Terraform for everything else. One issue per run.
            </p>
            <div><button className="aca-btn-primary" onClick={run}>▶ Run Agent</button></div>
          </div>
        )}

        {phase === 'thinking' && (
          <div style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 8, padding: 24, display: 'flex', flexDirection: 'column', gap: 18,
          }}>
            <div className="aca-row" style={{ gap: 10, color: 'var(--accent-blue)', fontSize: 12 }}>
              <span className="aca-dots blue"><span /><span /><span /></span>
              <span style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>Agent running</span>
            </div>
            <AgentPhaseSteps step={step} />
          </div>
        )}

        {phase === 'awaiting_approval' && data && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ borderLeft: '2px solid var(--warning)', padding: '8px 14px', background: 'var(--bg-panel)' }}>
              <div className="aca-row" style={{ gap: 10, marginBottom: 6 }}>
                <span className={'aca-badge ' + data.issue.severity.toLowerCase()}>{data.issue.severity}</span>
                <strong style={{ color: 'var(--text-primary)', fontSize: 14 }}>{data.issue.title}</strong>
              </div>
              <p style={{ margin: '6px 0', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.55 }}>
                {data.issue.description}
              </p>
              <div className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>{data.issue.resource_id}</div>
            </div>

            <div className="aca-summary">{data.summary}</div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="aca-btn-ghost small" onClick={() => setShowHcl((v) => !v)}>
                {showHcl ? '▾' : '▸'} Terraform HCL
              </button>
              <button className="aca-btn-ghost small" onClick={() => setShowPlan((v) => !v)}>
                {showPlan ? '▾' : '▸'} Plan output
              </button>
            </div>
            {showHcl  && <pre className="aca-code" style={{ maxHeight: 260, margin: 0 }}>{data.hcl}</pre>}
            {showPlan && <pre className="aca-code" style={{ maxHeight: 200, margin: 0 }}>{data.plan_output}</pre>}

            <div style={{
              background: 'var(--warning-dim)', border: '1px solid var(--warning)',
              borderRadius: 6, padding: '10px 14px', color: 'var(--warning)', fontSize: 12,
            }}>
              ⚠ This will modify your live AWS infrastructure.
            </div>

            <div className="aca-row" style={{ gap: 8 }}>
              <button className="aca-btn-primary" onClick={approve}>Approve & Apply</button>
              <button className="aca-btn-ghost small" onClick={reject}>Reject</button>
            </div>
          </div>
        )}

        {phase === 'applying' && (
          <div style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 8, padding: 24,
          }}>
            <div className="aca-row" style={{ gap: 10, color: 'var(--accent-blue)', fontSize: 13 }}>
              <span className="aca-dots blue"><span /><span /><span /></span>
              <span>Applying changes to AWS…</span>
            </div>
          </div>
        )}

        {phase === 'complete' && result && (
          <div style={{
            background: 'var(--success-dim)', border: '1px solid var(--success)',
            borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 12,
          }}>
            <div className="aca-row" style={{ gap: 10 }}>
              <span className="aca-badge success">✓ COMPLETE</span>
              <span style={{ color: 'var(--text-primary)', fontSize: 13 }}>Finding remediated successfully.</span>
            </div>
            <pre className="aca-code" style={{ maxHeight: 260, margin: 0, color: 'var(--success)' }}>
              {result.apply_output}
            </pre>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <button className="aca-btn-ghost small" onClick={verify} disabled={verifying}>
                {verifying ? '…Scanning' : '🔄 Re-scan & Verify'}
              </button>
              <button className="aca-btn-ghost small" onClick={downloadReport}>
                ⬇ Download Report
              </button>
              <button className="aca-btn-ghost small" onClick={() =>
                document.getElementById('execution-history-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }>
                📋 View History
              </button>
              {!resolved && !rollbackDone && (
                <button className="aca-btn-ghost small" onClick={markResolved}>
                  ✓ Mark Resolved
                </button>
              )}
              {!rollbackDone && data?.action_type === 'terraform_apply' && (
                <button className="aca-btn-ghost small" onClick={rollback} disabled={rollingBack}
                  style={{ color: 'var(--error)' }}>
                  {rollingBack ? 'Preparing destroy plan…' : 'Review destroy plan'}
                </button>
              )}
              <button className="aca-btn-ghost small"
                onClick={() => { setPhase('idle'); setData(null); setResult(null); setVerifyResult(null); setRollbackDone(false); setResolved(false) }}>
                ▶ Run Again
              </button>
            </div>
            {resolved && (
              <div style={{
                background: 'var(--success-dim)', border: '1px solid var(--success)',
                borderRadius: 6, padding: '8px 14px', fontSize: 12, color: 'var(--success)',
              }}>
                ✓ Marked as resolved — recorded in execution log.
              </div>
            )}
            {rollbackDone && (
              <div style={{
                background: 'var(--warning-dim)', border: '1px solid var(--warning)',
                borderRadius: 6, padding: '8px 14px', fontSize: 12, color: 'var(--warning)',
              }}>
                ↩ Reviewed destroy action completed.
              </div>
            )}
            {verifyResult && (
              <div style={{
                background: 'var(--warning-dim)',
                border:     '1px solid var(--warning)',
                borderRadius: 6, padding: '8px 14px', fontSize: 12,
                color: 'var(--warning)',
              }}>
                {verificationMessage(verifyResult)}
              </div>
            )}
          </div>
        )}

        {['failed', 'uncertain', 'invalidated', 'rejected'].includes(phase) && (
          <div style={{
            background: 'var(--error-dim)', border: '1px solid var(--error)',
            borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 10,
          }}>
            <div className="aca-row" style={{ gap: 10 }}>
              <PhasePill phase={phase} />
              <span style={{ color: 'var(--text-primary)', fontSize: 13 }}>{error || 'Agent failed'}</span>
            </div>
            {phase !== 'uncertain' && <div><button className="aca-btn-ghost small" onClick={run}>Start a new assessment</button></div>}
          </div>
        )}
      </div>
    </section>
  )
}

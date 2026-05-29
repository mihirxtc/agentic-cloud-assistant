import { useState, useEffect } from 'react'
import { callTool } from '../../api/mcpClient'
import { QUICK_REQUESTS } from '../../utils/constants'

const PHASE_BADGE = {
  planning:          ['blue',    'PLANNING'],
  awaiting_approval: ['warning', 'AWAITING APPROVAL'],
  applying:          ['blue',    'APPLYING'],
  complete:          ['success', 'COMPLETE'],
  failed:            ['error',   'FAILED'],
  rejected:          ['muted',   'REJECTED'],
}

function ExecutionInline({ hcl, description, awsAccessKey, awsSecretKey, awsRegion, model, apiKey, onApplyComplete, onDone }) {
  const [phase,        setPhase]        = useState('planning')
  const [planOutput,   setPlanOutput]   = useState('')
  const [applyOutput,  setApplyOutput]  = useState('')
  const [executionId,  setExecutionId]  = useState(null)
  const [error,        setError]        = useState(null)
  const [keyFiles,     setKeyFiles]     = useState([])
  const [verifying,    setVerifying]    = useState(false)
  const [verifyResult, setVerifyResult] = useState(null)
  const [rollingBack,  setRollingBack]  = useState(false)
  const [rollbackDone, setRollbackDone] = useState(false)

  const awsArgs = {
    aws_access_key_id:     awsAccessKey || '',
    aws_secret_access_key: awsSecretKey || '',
    aws_region:            awsRegion    || 'us-east-1',
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await callTool('run_terraform_plan_mcp', {
          hcl_config:  hcl,
          description: description || 'Terraform plan',
          ...awsArgs,
        })
        if (cancelled) return
        setPlanOutput(r.plan_output || '')
        setExecutionId(r.execution_id)
        setPhase(r.status === 'awaiting_approval' ? 'awaiting_approval' : 'failed')
      } catch (e) {
        if (!cancelled) {
          setError(e.message)
          setPhase('failed')
        }
      }
    })()
    return () => { cancelled = true }
  }, []) // eslint-disable-line

  const approve = async () => {
    setPhase('applying')
    try {
      const r = await callTool('run_terraform_apply_mcp', {
        execution_id: executionId,
        approved:     true,
        ...awsArgs,
      })
      setApplyOutput(r.apply_output || '')
      if (r.key_files?.length) setKeyFiles(r.key_files)
      const finalPhase = r.status === 'complete' ? 'complete' : 'failed'
      setPhase(finalPhase)
      if (finalPhase === 'complete') onApplyComplete?.()
    } catch (e) {
      setApplyOutput(e.message)
      setPhase('failed')
    }
  }

  const downloadPem = async (file) => {
    try {
      const res = await fetch(file.download_path)
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const text = await res.text()
      const blob = new Blob([text], { type: 'application/x-pem-file' })
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = file.name
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Failed to download key file: ${e.message}`)
    }
  }

  const downloadReport = () => {
    const report = {
      generated_at: new Date().toISOString(),
      execution_id: executionId,
      status:       'complete',
      description,
      plan_output:  planOutput,
      apply_output: applyOutput,
    }
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `audit-${executionId || 'report'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const rollback = async () => {
    if (!window.confirm('This will destroy the AWS resources just created. Are you sure?')) return
    setRollingBack(true)
    try {
      await callTool('rollback_execution', {
        execution_id:          executionId,
        aws_access_key_id:     awsAccessKey || '',
        aws_secret_access_key: awsSecretKey || '',
        aws_region:            awsRegion    || 'us-east-1',
      })
      setRollbackDone(true)
    } catch { /* silent */ }
    finally { setRollingBack(false) }
  }

  const verify = async () => {
    setVerifying(true)
    setVerifyResult(null)
    try {
      const r = await callTool('run_security_analysis_with_summary', { model: model || 'groq', api_key: apiKey || '', region: awsRegion || 'us-east-1' })
      setVerifyResult({ count: r.total_findings })
    } catch { /* silent */ }
    finally { setVerifying(false) }
  }

  const reject = async () => {
    try {
      await callTool('run_terraform_apply_mcp', {
        execution_id: executionId,
        approved:     false,
        ...awsArgs,
      })
    } catch { /* rejection is best-effort */ }
    setPhase('rejected')
  }

  const [badgeColor, badgeLabel] = PHASE_BADGE[phase] ?? ['blue', phase.toUpperCase()]

  const outputLines = [
    planOutput  ? `# terraform plan\n${planOutput}`  : null,
    phase === 'applying' && !applyOutput ? '# terraform apply\nApplying…' : null,
    applyOutput ? `# terraform apply\n${applyOutput}` : null,
  ].filter(Boolean).join('\n\n')

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8, padding: 14,
      display: 'flex', flexDirection: 'column', gap: 12, background: 'var(--bg-elevated)',
    }}>
      <div className="aca-row" style={{ justifyContent: 'space-between' }}>
        <span className="aca-panel-title">Execution</span>
        <span className={'aca-badge ' + badgeColor}>{badgeLabel}</span>
      </div>

      {phase === 'planning' && (
        <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
          Running terraform init + plan against AWS…
        </div>
      )}

      {outputLines && (
        <pre className="aca-code" style={{ maxHeight: 320, margin: 0 }}>{outputLines}</pre>
      )}

      {error && (
        <div style={{ color: 'var(--error, #f87171)', fontSize: 13 }}>{error}</div>
      )}

      {phase === 'awaiting_approval' && (
        <div className="aca-row" style={{ gap: 8 }}>
          <button className="aca-btn-primary" onClick={approve}>Approve &amp; Apply</button>
          <button className="aca-btn-ghost small" onClick={reject}>Reject</button>
        </div>
      )}

      {keyFiles.length > 0 && (
        <div style={{
          background: 'var(--warning-dim)', border: '1px solid var(--warning)',
          borderRadius: 6, padding: '10px 14px',
        }}>
          <div style={{ color: 'var(--warning)', fontSize: 12, marginBottom: 8 }}>
            SSH Private Key generated — download and store securely. This is the only time you can retrieve it.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {keyFiles.map((f) => (
              <button key={f.name} className="aca-btn-primary" onClick={() => downloadPem(f)}>
                ↓ Download {f.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {(phase === 'complete' || phase === 'failed' || phase === 'rejected') && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {phase === 'complete' && (
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
              {!rollbackDone && (
                <button className="aca-btn-ghost small" onClick={rollback} disabled={rollingBack}
                  style={{ color: 'var(--error)' }}>
                  {rollingBack ? '…Rolling back' : '↩ Rollback'}
                </button>
              )}
              <button className="aca-btn-ghost small" onClick={onDone}>↩ New Request</button>
            </div>
          )}
          {phase !== 'complete' && (
            <button className="aca-btn-ghost small" onClick={onDone}>↩ New Request</button>
          )}
          {rollbackDone && (
            <div style={{
              background: 'var(--warning-dim)', border: '1px solid var(--warning)',
              borderRadius: 6, padding: '8px 12px', fontSize: 12, color: 'var(--warning)',
            }}>
              ↩ Rollback complete — created resources have been destroyed.
            </div>
          )}
          {verifyResult && (
            <div style={{
              background: verifyResult.count === 0 ? 'var(--success-dim)' : 'var(--warning-dim)',
              border:     `1px solid ${verifyResult.count === 0 ? 'var(--success)' : 'var(--warning)'}`,
              borderRadius: 6, padding: '8px 12px', fontSize: 12,
              color: verifyResult.count === 0 ? 'var(--success)' : 'var(--warning)',
            }}>
              {verifyResult.count === 0
                ? '✓ Re-scan complete — 0 issues found.'
                : `⚠ Re-scan found ${verifyResult.count} remaining issue(s).`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function TerraformPanel({ model, apiKey, awsAccessKey, awsSecretKey, awsRegion, prefill, onPrefillConsumed, onApplyComplete, }) {
  const [input,    setInput]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const [result,   setResult]   = useState(null)
  const [copied,   setCopied]   = useState(false)
  const [showExec, setShowExec] = useState(false)

  useEffect(() => {
    if (prefill) {
      setInput(prefill)
      setResult(null)
      setShowExec(false)
      onPrefillConsumed?.()
    }
  }, [prefill]) // eslint-disable-line

  const generate = async (req) => {
    const request = (req ?? input).trim()
    if (!request || loading) return
    if (req) setInput(req)
    setLoading(true)
    setResult(null)
    setShowExec(false)
    try {
      const r = await callTool('generate_terraform_from_request', {
        request,
        model,
        api_key: apiKey,
        aws_access_key_id:     awsAccessKey || '',
        aws_secret_access_key: awsSecretKey || '',
        aws_region:            awsRegion    || 'us-east-1',
      })
      setResult(r)
    } catch (e) {
      setResult({ error: e.message || 'Generation failed', hcl: '', validation: { valid: false, message: '' } })
    } finally { setLoading(false) }
  }

  const copy = () => {
    if (!result) return
    navigator.clipboard?.writeText(result.hcl)
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  const download = () => {
    const blob = new Blob([result.hcl], { type: 'text/plain' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = 'main.tf'
    a.click()
    URL.revokeObjectURL(url)
  }

  const modelBadge = model === 'groq' ? 'GROQ' : model === 'anthropic' ? 'ANTHROPIC' : 'OLLAMA'

  return (
    <section id="terraform-panel" className="aca-panel" style={{ gridColumn: 'span 12' }}>
      <div className="aca-panel-hd">
        <div className="aca-row" style={{ gap: 10 }}>
          <span className="aca-panel-title">Terraform Generator</span>
          <span className="aca-badge blue">{modelBadge}</span>
        </div>
      </div>
      <div className="aca-panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {QUICK_REQUESTS.map((q) => (
            <button key={q} className="aca-pill-btn" onClick={() => generate(q)}>{q}</button>
          ))}
        </div>
        <div className="aca-row" style={{ gap: 8 }}>
          <input
            className="aca-input"
            placeholder="Describe the resource you want to create…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') generate() }}
          />
          <button className="aca-btn-primary"
            onClick={() => generate()} disabled={loading || !input.trim()}
            style={{ whiteSpace: 'nowrap' }}>
            {loading ? 'Generating…' : 'Generate'}
          </button>
        </div>

        {loading && <div className="aca-skel" style={{ height: 260 }} />}

        {result && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="aca-row" style={{ gap: 10, flexWrap: 'wrap' }}>
              <span className="aca-badge muted mono">{result.resource_type}</span>
              <span className={'aca-badge ' + (result.validation?.valid ? 'success' : 'error')}>
                {result.validation?.valid ? '✓ VALIDATED' : '✗ INVALID'}
              </span>
              <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{result.description}</span>
            </div>

            {result.error && (
              <div style={{
                background: 'var(--error-dim, rgba(255,68,68,0.08))', border: '1px solid var(--error, #f87171)',
                borderRadius: 6, padding: '8px 12px', color: 'var(--error, #f87171)', fontSize: 12,
                fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              }}>
                {result.error}
              </div>
            )}

            {!result.error && result.validation && !result.validation.valid && result.validation.message && (
              <div style={{
                background: 'var(--error-dim, rgba(255,68,68,0.08))', border: '1px solid var(--error, #f87171)',
                borderRadius: 6, padding: '8px 12px', color: 'var(--error, #f87171)', fontSize: 12,
                fontFamily: 'monospace', whiteSpace: 'pre-wrap',
              }}>
                {result.validation.message}
              </div>
            )}

            {result.naming_note && (
              <div style={{
                background: 'var(--warning-dim)', border: '1px solid var(--border)',
                borderRadius: 6, padding: '8px 12px', color: 'var(--warning)', fontSize: 12,
              }}>
                ℹ {result.naming_note}
              </div>
            )}

            <div style={{ position: 'relative' }}>
              <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 2, display: 'flex', gap: 6 }}>
                <button className="aca-btn-ghost small"
                  style={{ background: 'var(--bg-elevated)' }}
                  onClick={download}>
                  ↓ Download
                </button>
                <button className="aca-btn-ghost small" onClick={copy}
                  style={{ background: 'var(--bg-elevated)' }}>
                  {copied ? '✓ Copied' : 'Copy'}
                </button>
              </div>
              <pre className="aca-code" style={{ maxHeight: 360, margin: 0 }}>{result.hcl}</pre>
            </div>

            <div>
              <button className="aca-btn-primary" onClick={() => setShowExec(true)} disabled={showExec || !result.validation?.valid}>
                🚀 Run Plan & Deploy
              </button>
            </div>

            {showExec && (
              <ExecutionInline
                hcl={result.hcl} description={input}
                awsAccessKey={awsAccessKey} awsSecretKey={awsSecretKey} awsRegion={awsRegion}
                model={model} apiKey={apiKey}
                onApplyComplete={onApplyComplete}
                onDone={() => { setShowExec(false); setResult(null); setInput('') }}
              />
            )}
          </div>
        )}
      </div>
    </section>
  )
}

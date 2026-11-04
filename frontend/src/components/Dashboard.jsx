import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from '../contexts/useAuth'
import { useApiKeys } from '../contexts/useApiKeys'
import { isAssessmentComplete } from '../utils/assessment'
import { computeHealthScore } from '../utils/scoring'
import { callTool } from '../api/mcpClient'
import toast from 'react-hot-toast'
import JobsPanel from './panels/JobsPanel'
import Topbar                from './ui/Topbar'
import InfrastructurePanel   from './panels/InfrastructurePanel'
import SecurityPanel         from './panels/SecurityPanel'
import CostPanel             from './panels/CostPanel'
import ChatPanel             from './panels/ChatPanel'
import TerraformPanel        from './panels/TerraformPanel'
import ExecutionHistoryPanel from './panels/ExecutionHistoryPanel'
import SecurityAgentPanel    from './panels/SecurityAgentPanel'
import KnowledgeBasePanel    from './panels/KnowledgeBasePanel'

const AUTO_REFRESH_MS = 90_000 // re-scan every 90 s once data is loaded

export default function Dashboard({ onOpenSettings }) {
  const { keys, serverConfig, awsIdentity, awsIdentityError, awsIdentityLoading, refreshAwsIdentity } = useApiKeys()
  const { logout } = useAuth()

  const [infra,           setInfra]           = useState(null)
  const [infraLoading,    setInfraLoading]    = useState(false)
  const [security,        setSecurity]        = useState(null)
  const [securityLoading, setSecurityLoading] = useState(false)
  const [cost,            setCost]            = useState(null)
  const [costLoading,     setCostLoading]     = useState(false)
  const [prefill,         setPrefill]         = useState(null)
  const [securityScore,   setSecurityScore]   = useState(null)
  const [lastRefresh,     setLastRefresh]     = useState(null)

  const infraLoadingRef = useRef(false)

  const runInfra = useCallback(async () => {
    if (infraLoadingRef.current) return
    infraLoadingRef.current = true
    setInfraLoading(true)
    try {
      setInfra(await callTool('full_aws_scan', { region: keys.region || 'us-east-1' }))
      setLastRefresh(new Date())
    } catch (error) { setInfra(null); toast.error(error.message) } finally {
      setInfraLoading(false)
      infraLoadingRef.current = false
    }
  }, [keys.region])

  // Auto-refresh every 90 s — but only after the first manual scan
  useEffect(() => {
    if (!infra) return
    const id = setInterval(runInfra, AUTO_REFRESH_MS)
    return () => clearInterval(id)
  }, [infra, runInfra])

  const runSecurity = useCallback(async () => {
    setSecurityLoading(true)
    try {
      const d = await callTool('run_security_analysis_with_summary', {
        model:   keys.model,
        region:  keys.region || 'us-east-1',
      })
      setSecurity(d)
      setSecurityScore(isAssessmentComplete(d) ? computeHealthScore(d.findings) : null)
    } catch (error) { setSecurity(null); setSecurityScore(null); toast.error(error.message) } finally { setSecurityLoading(false) }
  }, [keys.model, keys.region])

  const runCost = async () => {
    setCostLoading(true)
    try {
      setCost(await callTool('get_cost_with_summary', {
        model:   keys.model,
      }))
    } catch (error) { setCost(null); toast.error(error.message) } finally { setCostLoading(false) }
  }

  const onFix = (finding) => {
    setPrefill(`Fix ${finding.rule} on ${finding.resource_id}: ${finding.recommendation}`)
    setTimeout(() => {
      document.getElementById('terraform-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 50)
  }

  // Direct AWS API fix for SSH_PORT_OPEN / RDP_PORT_OPEN — no Terraform needed.
  // Calls ec2.revoke_security_group_ingress() to remove the 0.0.0.0/0 rule.
  const onDirectFix = useCallback(async (finding) => {
    const portMap = { SSH_PORT_OPEN: 22, RDP_PORT_OPEN: 3389 }
    const port = portMap[finding.rule]
    if (!port) throw new Error(`No direct fix available for rule ${finding.rule}`)
    const result = await callTool('revoke_open_ingress_rule', {
      idempotency_key: crypto.randomUUID(),
      sg_id:                 finding.resource_id,
      scan_id:               finding.scan_id,
      port,
      region:                keys.region            || 'us-east-1',
    })
    if (result.status !== 'awaiting_approval') throw new Error(result.error || 'No reviewable action was prepared')
    return result
  }, [keys])

  // Called by TerraformPanel / SecurityAgentPanel after a successful apply
  const onInfraChanged = useCallback(() => {
    setTimeout(runInfra, 3000) // brief delay so AWS state settles
  }, [runInfra])

  const hasKeys = !!serverConfig?.providers?.[keys.model]

  return (
    <div>
      <Topbar
        securityScore={securityScore}
        onOpenSettings={onOpenSettings}
        onLogout={async () => { try { await logout() } catch (error) { toast.error(error.message) } }}
        region={keys.region || 'us-east-1'}
        hasKeys={hasKeys}
        awsIdentity={awsIdentity}
      />
      <div role="status" style={{ padding: '12px 24px', color: 'var(--text-secondary)', fontSize: 12 }}>
        {awsIdentityLoading ? 'Verifying AWS account…' : awsIdentityError || (awsIdentity
          ? `Verified account ${awsIdentity.account_id} · ${awsIdentity.region}` : 'AWS connection is not verified')}
        <button className="aca-btn-ghost small" style={{ marginLeft: 12 }} onClick={refreshAwsIdentity} disabled={awsIdentityLoading}>
          Verify connection
        </button>
      </div>
      <div className="aca-grid">
        <fieldset aria-label="AWS operations" disabled={!awsIdentity} style={{ display: 'contents' }}>
        <InfrastructurePanel
          data={infra} loading={infraLoading}
          onScan={runInfra} lastRefresh={lastRefresh}
        />
        <SecurityPanel data={security} loading={securityLoading} onScan={runSecurity} onFix={onFix} onDirectFix={onDirectFix} />
        <CostPanel data={cost} loading={costLoading} onLoad={runCost} />
        <ChatPanel model={keys.model || 'groq'} />
        <TerraformPanel
          model={keys.model || 'groq'}
          awsRegion={keys.region || 'us-east-1'}
          prefill={prefill} onPrefillConsumed={() => setPrefill(null)}
          onApplyComplete={onInfraChanged}
        />
        <SecurityAgentPanel
          region={keys.region || 'us-east-1'}
          model={keys.model || 'groq'}
          onApplyComplete={onInfraChanged}
        />
        </fieldset>
        <JobsPanel />
        <ExecutionHistoryPanel />
        <KnowledgeBasePanel />
      </div>
    </div>
  )
}

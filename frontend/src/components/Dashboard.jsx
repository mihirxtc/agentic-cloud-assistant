import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { useApiKeys } from '../contexts/ApiKeyContext'
import { computeHealthScore } from '../utils/scoring'
import { callTool } from '../api/mcpClient'
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
  const { keys }   = useApiKeys()
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

  const getApiKey = () => {
    if (keys.model === 'groq')      return keys.groq_key
    if (keys.model === 'anthropic') return keys.anthropic_key
    return keys.ollama_url
  }

  const runInfra = useCallback(async () => {
    if (infraLoadingRef.current) return
    infraLoadingRef.current = true
    setInfraLoading(true)
    try {
      setInfra(await callTool('full_aws_scan', { region: keys.region || 'us-east-1' }))
      setLastRefresh(new Date())
    } finally {
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

  const runSecurity = async () => {
    setSecurityLoading(true)
    try {
      const d = await callTool('run_security_analysis_with_summary', {
        model:   keys.model,
        api_key: getApiKey(),
        region:  keys.region || 'us-east-1',
      })
      setSecurity(d)
      setSecurityScore(computeHealthScore(d.findings))
    } finally { setSecurityLoading(false) }
  }

  const runCost = async () => {
    setCostLoading(true)
    try {
      setCost(await callTool('get_cost_with_summary', {
        model:   keys.model,
        api_key: getApiKey(),
      }))
    } finally { setCostLoading(false) }
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
      sg_id:                 finding.resource_id,
      port,
      region:                keys.region            || 'us-east-1',
      aws_access_key_id:     keys.aws_access_key    || '',
      aws_secret_access_key: keys.aws_secret_key    || '',
    })
    if (!result.success) throw new Error(result.message)
    // Re-scan security after a short delay so AWS state settles
    setTimeout(runSecurity, 2500)
    return result
  }, [keys, runSecurity])

  // Called by TerraformPanel / SecurityAgentPanel after a successful apply
  const onInfraChanged = useCallback(() => {
    setTimeout(runInfra, 3000) // brief delay so AWS state settles
  }, [runInfra])

  const hasKeys = !!(keys.groq_key || keys.anthropic_key || keys.ollama_url)

  return (
    <div>
      <Topbar
        securityScore={securityScore}
        onOpenSettings={onOpenSettings}
        onLogout={logout}
        region={keys.region || 'us-east-1'}
        hasKeys={hasKeys}
      />
      <div className="aca-grid">
        <InfrastructurePanel
          data={infra} loading={infraLoading}
          onScan={runInfra} lastRefresh={lastRefresh}
        />
        <SecurityPanel data={security} loading={securityLoading} onScan={runSecurity} onFix={onFix} onDirectFix={onDirectFix} />
        <CostPanel data={cost} loading={costLoading} onLoad={runCost} />
        <ChatPanel model={keys.model || 'groq'} apiKey={getApiKey()} />
        <TerraformPanel
          model={keys.model || 'groq'} apiKey={getApiKey()}
          awsAccessKey={keys.aws_access_key} awsSecretKey={keys.aws_secret_key}
          awsRegion={keys.region || 'us-east-1'}
          prefill={prefill} onPrefillConsumed={() => setPrefill(null)}
          onApplyComplete={onInfraChanged}
        />
        <ExecutionHistoryPanel />
        <SecurityAgentPanel
          region={keys.region || 'us-east-1'}
          model={keys.model || 'groq'}
          apiKey={getApiKey()}
          awsAccessKey={keys.aws_access_key}
          awsSecretKey={keys.aws_secret_key}
          onApplyComplete={onInfraChanged}
        />
        <KnowledgeBasePanel />
      </div>
    </div>
  )
}

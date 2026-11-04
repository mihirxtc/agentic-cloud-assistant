import { authFetch } from './session.js'

const regionParameters = {
  full_aws_scan: 'region', scan_ec2_instances: 'region', scan_s3_buckets: 'region', scan_iam_users: 'region',
  scan_security_groups_detail: 'region', scan_vpc_detail: 'region', scan_sg_usage_tool: 'region',
  run_security_analysis_with_summary: 'region', estimate_costs: 'region', get_cost_with_summary: 'region',
  generate_terraform_hcl: 'region', generate_terraform_from_request: 'aws_region',
  run_terraform_plan_mcp: 'aws_region', run_terraform_apply_mcp: 'aws_region', aws_chat: 'region',
  inspect_interrupted_action: 'aws_region', reconcile_action: 'aws_region',
  agent_run: 'region', agent_approve: 'aws_region', revoke_open_ingress_rule: 'region', rollback_execution: 'aws_region',
}
let state = { identity: null, region: null, loading: false, error: null }
let revision = 0
let controller = null
const listeners = new Set()
export function getAwsState() { return state }
export function subscribeAwsState(listener) { listeners.add(listener); return () => listeners.delete(listener) }
function publish(value) { state = value; listeners.forEach((listener) => listener()) }
export function clearAwsIdentity() {
  revision += 1
  controller?.abort()
  publish({ identity: null, region: null, loading: false, error: null })
}
export async function refreshAwsIdentity(region) {
  const requestRevision = ++revision
  controller?.abort()
  controller = new AbortController()
  publish({ region, identity: null, loading: true, error: null })
  try {
    const response = await authFetch(`/auth/aws-identity?region=${encodeURIComponent(region)}`, { signal: controller.signal })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Unable to verify AWS identity')
    if (result.aws_identity?.region !== region || !/^\d{12}$/.test(result.aws_identity?.account_id ?? '')) {
      throw new Error('Server returned an invalid AWS identity')
    }
    if (revision === requestRevision) publish({ region, identity: result.aws_identity, loading: false, error: null })
  } catch (error) {
    if (revision === requestRevision) publish({ region, identity: null, loading: false, error: error.message })
  }
}
export function withAwsIdentity(name, args) {
  const regionParameter = regionParameters[name]
  if (!regionParameter || args.approved === false) return args
  const identity = state.identity
  if (!identity) throw new Error('Verify the AWS connection before continuing')
  if ((args[regionParameter] && args[regionParameter] !== identity.region)
      || (args.expected_account_id && args.expected_account_id !== identity.account_id)) {
    throw new Error('This operation belongs to another AWS account or region. Verify its original connection.')
  }
  return { ...args, [regionParameter]: identity.region, expected_account_id: identity.account_id }
}

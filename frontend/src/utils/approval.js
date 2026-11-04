export function decisionArgs(proposal, approved, idempotencyKey) {
  if (!proposal?.execution_id) throw new Error('No action is available for review')
  if (approved && (proposal.status !== 'awaiting_approval' || !proposal.approval_digest)) {
    throw new Error('A saved reviewed proposal is required')
  }
  return { execution_id: proposal.execution_id, approved,
    approval_digest: proposal.approval_digest || '', idempotency_key: `${idempotencyKey}_${proposal.execution_id}` }
}

export function actionMessage(result) {
  if (result.status === 'uncertain') return 'Outcome uncertain. Inspect AWS and Terraform state; do not retry the mutation.'
  if (result.status === 'applying' || result.status === 'planning') return 'This action is already in progress. Check execution history; do not submit another approval.'
  return result.error || result.apply_output || `Action ${result.status}`
}

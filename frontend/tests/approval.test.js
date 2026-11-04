import test from 'node:test'
import assert from 'node:assert/strict'
import { decisionArgs, actionMessage } from '../src/utils/approval.js'
const proposal = { execution_id: 'exec-one', status: 'awaiting_approval', approval_digest: 'reviewed-bytes' }

test('approval binds the reviewed digest, action and stable request key', () => {
  const args = decisionArgs(proposal, true, 'request-one')
  assert.equal(args.approval_digest, 'reviewed-bytes')
  assert.deepEqual(args, decisionArgs(proposal, true, 'request-one'))
  assert.notEqual(args.idempotency_key, decisionArgs({ ...proposal, execution_id: 'exec-two' }, true, 'request-one').idempotency_key)
})
test('legacy, missing and non-awaiting proposals cannot be approved', () => {
  for (const value of [null, { execution_id: 'old' }, { ...proposal, status: 'complete' }]) {
    assert.throws(() => decisionArgs(value, true, 'request-one'))
  }
  assert.equal(decisionArgs({ execution_id: 'old' }, false, 'reject-old').approved, false)
})
test('uncertain and active outcomes instruct the operator not to repeat mutation', () => {
  assert.match(actionMessage({ status: 'uncertain' }), /do not retry/)
  assert.match(actionMessage({ status: 'applying' }), /already in progress/)
})

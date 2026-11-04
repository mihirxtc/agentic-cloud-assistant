import test from 'node:test'
import assert from 'node:assert/strict'
import { isAssessmentComplete, inventoryValue, verificationMessage } from '../src/utils/assessment.js'

test('incomplete or legacy responses cannot receive a complete assessment score', () => {
  for (const status of ['partial', 'failed', undefined]) {
    assert.equal(isAssessmentComplete({ status, findings: [] }), false)
    assert.match(verificationMessage({ status, total_findings: 0 }), /not been verified/)
  }
  assert.equal(isAssessmentComplete({ status: 'complete', coverage: { status: 'partial' } }), false)
  assert.equal(isAssessmentComplete({ status: 'complete', coverage: { status: 'complete' } }), true)
})

test('inventory distinguishes unknown from a verified empty service', () => {
  assert.equal(inventoryValue(undefined), null)
  assert.equal(inventoryValue({ status: 'error', count: 0 }), null)
  assert.equal(inventoryValue({ status: 'ok', coverage: 'complete', count: 0 }), 0)
  assert.equal(inventoryValue({ status: 'partial', count: 2 }), 2)
  assert.equal(inventoryValue({ status: 'ok', user_count: 3 }, 'user_count'), 3)
})

test('complete zero-finding rescans do not claim an individual fix is confirmed', () => {
  const message = verificationMessage({ status: 'complete', coverage: { status: 'complete' }, total_findings: 0 })
  assert.match(message, /Review whether the original issue is resolved/)
  assert.doesNotMatch(message, /Fix confirmed/)
})

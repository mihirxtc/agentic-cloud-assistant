import test from 'node:test'
import assert from 'node:assert/strict'
import { computeHealthScore } from '../src/utils/scoring.js'
import { costView } from '../src/utils/cost.js'

test('scores require complete coverage and the backend scoring contract', () => {
  const data = { status: 'complete', coverage: { status: 'complete' }, health_score: { method: 'weighted_applicable_rule_families_v1', value: 50 } }
  assert.equal(computeHealthScore(data), 50)
  for (const value of [null, undefined, NaN, -1, 101]) assert.equal(computeHealthScore({ ...data, health_score: { ...data.health_score, value } }), null)
  assert.equal(computeHealthScore({ ...data, status: 'partial' }), null)
  assert.equal(computeHealthScore({ ...data, health_score: { value: 100 } }), null)
  assert.equal(computeHealthScore([]), null)
})
test('cost percentage uses the backend completed-month comparison, never month-to-date totals', () => {
  const data = { current_month: { status: 'complete', amount: 3, currency: 'EUR' },
    monthly_trend: [{ amount: 100 }, { amount: 120 }], comparison: { status: 'complete', percentage: 20,
      previous_period: { start: '2026-09-01' }, current_period: { start: '2026-10-01' } } }
  assert.equal(costView(data).percentage, 20)
  assert.equal(costView(data).amount, 3)
  assert.match(costView(data).comparisonLabel, /2026-09 → 2026-10/)
  assert.equal(costView({ ...data, comparison: undefined }).percentage, null)
})
test('billing failures are unavailable and real zero stays zero', () => {
  assert.equal(costView({ current_month: { amount: 0, status: 'failed' } }).amount, null)
  assert.equal(costView({ current_month: { amount: 0, status: 'complete' } }).amount, 0)
  assert.equal(costView({}).percentage, null)
})

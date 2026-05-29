import { RULE_WEIGHTS, RULE_TOTAL } from './constants'

export function computeHealthScore(findings) {
  let failing = 0
  findings.forEach((f) => { failing += RULE_WEIGHTS[f.rule] || 0 })
  return Math.round((Math.max(0, RULE_TOTAL - failing) / RULE_TOTAL) * 100)
}

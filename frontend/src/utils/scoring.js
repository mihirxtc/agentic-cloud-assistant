import { isAssessmentComplete } from './assessment.js'

// The backend owns the method/weights and counts only applicable check families.
export function computeHealthScore(assessment) {
  if (!isAssessmentComplete(assessment)) return null
  const score = assessment.health_score
  return score?.method === 'weighted_applicable_rule_families_v1'
    && Number.isFinite(score.value) && score.value >= 0 && score.value <= 100
    ? score.value : null
}

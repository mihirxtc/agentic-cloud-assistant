export function costView(data) {
  const current = data?.current_month
  const amount = current?.status === 'complete' && Number.isFinite(current.amount) ? current.amount : null
  const comparison = data?.comparison
  const percentage = comparison?.status === 'complete' && comparison.previous_period?.start && comparison.current_period?.start && Number.isFinite(comparison.percentage) ? comparison.percentage : null
  return { amount, currency: current?.currency || 'currency unavailable', percentage,
    comparisonLabel: percentage == null ? 'Comparison unavailable' : `${comparison.previous_period.start.slice(0, 7)} → ${comparison.current_period.start.slice(0, 7)}` }
}

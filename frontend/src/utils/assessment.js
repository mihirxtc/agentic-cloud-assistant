// Missing coverage is unknown, including responses from older servers.
export function isAssessmentComplete(data) {
  return data?.status === 'complete' && data?.coverage?.status === 'complete'
}

export function inventoryValue(service, countKey = 'count') {
  if (!service || service.coverage === 'failed' || service.status === 'error') return null
  const count = service[countKey]
  return Number.isFinite(count) ? count : null
}

export function verificationMessage(data) {
  const count = data?.total_findings ?? 0
  if (!isAssessmentComplete(data)) {
    return `Verification incomplete (${data?.status || 'unknown'}): ${count} confirmed finding(s). The fix has not been verified.`
  }
  return `Completed built-in checks: ${count} finding(s). Review whether the original issue is resolved; this scan does not prove the account is secure.`
}

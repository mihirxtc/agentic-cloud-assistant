import { authFetch } from './session.js'

export const activeStatuses = new Set(['queued', 'running'])

async function read(path, options) {
  const response = await authFetch(path, options)
  if (!response.ok) throw new Error(`Unable to access background jobs (${response.status}). Reconnect and check Background Jobs before submitting again.`)
  return response.json()
}
export function listJobs() { return read('/jobs') }
export function getJob(id) { return read(`/jobs/${encodeURIComponent(id)}`) }
export function cancelJob(id) { return read(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }) }

export async function waitForJob(initial, { onJob, signal, poll = getJob, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) } = {}) {
  let job = initial
  while (true) {
    if (signal?.aborted) throw new Error(`Stopped watching ${job.job_id}. The backend job continues; check Background Jobs.`)
    onJob?.(job)
    if (!activeStatuses.has(job.status)) {
      if (job.status === 'complete') return job.result
      if (job.status === 'reconciliation_required' && job.result) return job.result
      throw new Error(`${job.error || job.stage}. Job ${job.job_id}; inspect Background Jobs before creating another request.`)
    }
    await pause(1000)
    try { job = await poll(job.job_id) }
    catch (error) { throw new Error(`${error.message} Job ${job.job_id} may still be running.`) }
  }
}

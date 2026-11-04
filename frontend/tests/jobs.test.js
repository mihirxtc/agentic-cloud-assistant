import test from 'node:test'
import assert from 'node:assert/strict'
import { waitForJob } from '../src/api/jobs.js'

test('watching a persisted job follows backend stages and restores result', async () => {
  const stages = []
  const responses = [{ job_id: 'job-1', status: 'running', stage: 'Scanning IAM' },
    { job_id: 'job-1', status: 'complete', stage: 'Finished', result: { findings: [] } }]
  const result = await waitForJob({ job_id: 'job-1', status: 'queued', stage: 'Queued' }, {
    poll: async (id) => { assert.equal(id, 'job-1'); return responses.shift() },
    pause: async () => {}, onJob: (job) => stages.push(job.stage),
  })
  assert.deepEqual(stages, ['Queued', 'Scanning IAM', 'Finished'])
  assert.deepEqual(result, { findings: [] })
})
test('disconnect preserves job identity and does not resubmit work', async () => {
  let calls = 0
  await assert.rejects(waitForJob({ job_id: 'job-2', status: 'running' }, {
    poll: async () => { calls++; throw new Error('Disconnected') }, pause: async () => {},
  }), /job-2 may still be running/)
  assert.equal(calls, 1)
})
test('interrupted work requires inspection and uncertain results remain uncertain', async () => {
  await assert.rejects(waitForJob({ job_id: 'job-3', status: 'interrupted', stage: 'Worker stopped' }), /inspect Background Jobs/)
  const result = await waitForJob({ job_id: 'job-4', status: 'reconciliation_required', result: { status: 'uncertain' } })
  assert.equal(result.status, 'uncertain')
})
test('stopping a watcher does not claim the backend was cancelled', async () => {
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(waitForJob({ job_id: 'job-5', status: 'running' }, { signal: controller.signal }), /backend job continues/)
})

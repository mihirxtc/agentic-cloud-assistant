import test from 'node:test'
import assert from 'node:assert/strict'

let moduleNumber = 0
const fresh = () => import(`../src/api/awsIdentity.js?test=${moduleNumber++}`)
const identity = { account_id: '111111111111', region: 'eu-west-1', partition: 'aws', arn: 'test-principal' }
const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

test('verification establishes an account and injects it into every AWS request', async (t) => {
  const aws = await fresh()
  t.mock.method(globalThis, 'fetch', async () => json({ aws_identity: identity }))
  await aws.refreshAwsIdentity(identity.region)
  assert.equal(aws.getAwsState().identity.account_id, identity.account_id)
  assert.deepEqual(aws.withAwsIdentity('run_terraform_plan_mcp', { hcl_config: 'test' }), {
    hcl_config: 'test', aws_region: identity.region, expected_account_id: identity.account_id,
  })
  assert.equal(aws.withAwsIdentity('get_cost_with_summary', {}).region, identity.region)
})

test('unverified actions and different regions are rejected; local rejection and RAG still work', async (t) => {
  const aws = await fresh()
  assert.throws(() => aws.withAwsIdentity('agent_run', {}), /Verify/)
  assert.deepEqual(aws.withAwsIdentity('rag_query_tool', { question: 'test' }), { question: 'test' })
  assert.deepEqual(aws.withAwsIdentity('agent_approve', { approved: false }), { approved: false })
  t.mock.method(globalThis, 'fetch', async () => json({ aws_identity: identity }))
  await aws.refreshAwsIdentity(identity.region)
  assert.throws(() => aws.withAwsIdentity('run_terraform_apply_mcp', { aws_region: 'us-east-1' }), /another AWS/)
})

test('failed verification clears stale account state and prevents actions', async (t) => {
  const aws = await fresh()
  let available = true
  t.mock.method(globalThis, 'fetch', async () => available ? json({ aws_identity: identity }) : json({ detail: 'STS unavailable' }, 503))
  await aws.refreshAwsIdentity(identity.region)
  available = false
  await aws.refreshAwsIdentity(identity.region)
  assert.equal(aws.getAwsState().identity, null)
  assert.equal(aws.getAwsState().error, 'STS unavailable')
  assert.throws(() => aws.withAwsIdentity('full_aws_scan', {}), /Verify/)
})

test('a late region response cannot replace the current verified region', async (t) => {
  const aws = await fresh()
  let finishOld
  t.mock.method(globalThis, 'fetch', (url) => url.searchParams.get('region') === 'eu-west-1'
    ? new Promise((resolve) => { finishOld = resolve })
    : Promise.resolve(json({ aws_identity: { ...identity, region: 'us-east-1' } })))
  const old = aws.refreshAwsIdentity('eu-west-1')
  await aws.refreshAwsIdentity('us-east-1')
  finishOld(json({ aws_identity: identity }))
  await old
  assert.equal(aws.getAwsState().identity.region, 'us-east-1')
})

test('logout clears verification and invalidates in-flight identity lookups', async (t) => {
  const aws = await fresh()
  let resolve
  t.mock.method(globalThis, 'fetch', () => new Promise((done) => { resolve = done }))
  const pending = aws.refreshAwsIdentity(identity.region)
  aws.clearAwsIdentity()
  resolve(json({ aws_identity: identity }))
  await pending
  assert.equal(aws.getAwsState().identity, null)
})

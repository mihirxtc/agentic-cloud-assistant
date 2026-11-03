import test from 'node:test'
import assert from 'node:assert/strict'

let moduleNumber = 0
const freshSession = () => import(`../src/api/session.js?test=${moduleNumber++}`)
const identity = { authenticated: true, username: 'operator', csrf_token: 'test-csrf', expires_at: 123 }
const json = (body, status = 200) => new Response(JSON.stringify(body), { status })

// All requests are mocked: these tests never contact a server or store a cookie.
test('login uses JSON and a custom header; mutations carry the in-memory CSRF token', async (t) => {
  const auth = await freshSession()
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return json(identity)
  })
  await auth.signIn('operator', 'test-password')
  assert.equal(auth.getSession().username, 'operator')
  assert.equal(requests[0].url.pathname, '/auth/login')
  assert.equal(requests[0].options.headers.get('X-ACA-Client'), 'browser')
  assert.equal(requests[0].options.credentials, 'same-origin')
  assert.equal(requests[0].options.redirect, 'error')
  await auth.authFetch('/mcp/', { method: 'POST', body: '{}' })
  assert.equal(requests[1].options.headers.get('X-CSRF-Token'), 'test-csrf')
})

test('session restoration is deduplicated and not based on browser storage', async (t) => {
  const auth = await freshSession()
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => { calls += 1; return json(identity) })
  await Promise.all([auth.restoreSession(), auth.restoreSession()])
  assert.equal(calls, 1)
  assert.equal(auth.getSession().authenticated, true)
})

test('a protected 401 clears memory and notifies the UI', async (t) => {
  const auth = await freshSession()
  t.mock.method(globalThis, 'fetch', async (url) => json(url.pathname === '/auth/login' ? identity : {}, url.pathname === '/auth/login' ? 200 : 401))
  await auth.signIn('operator', 'test-password')
  let notified = false
  const unsubscribe = auth.subscribeSession(() => { notified = true })
  await auth.authFetch('/auth/config')
  assert.equal(auth.getSession(), null)
  assert.equal(notified, true)
  unsubscribe()
})

test('stale requests cannot clear a newly established session', async (t) => {
  const auth = await freshSession()
  let resolveOld
  t.mock.method(globalThis, 'fetch', (url) => url.pathname === '/auth/login'
    ? Promise.resolve(json(identity)) : new Promise((resolve) => { resolveOld = resolve }))
  const pending = auth.authFetch('/auth/config')
  await auth.signIn('operator', 'test-password')
  resolveOld(json({}, 401))
  await pending
  assert.equal(auth.getSession().authenticated, true)
})

test('logout clears state only after revocation succeeds or the session is already expired', async (t) => {
  const auth = await freshSession()
  let logoutStatus = 503
  t.mock.method(globalThis, 'fetch', async (url) => url.pathname === '/auth/login'
    ? json(identity) : json({}, logoutStatus))
  await auth.signIn('operator', 'test-password')
  await assert.rejects(auth.signOut(), /Sign-out failed/)
  assert.equal(auth.getSession().authenticated, true)
  logoutStatus = 200
  await auth.signOut()
  assert.equal(auth.getSession(), null)
})

test('off-origin requests cannot receive credentials or CSRF headers', async (t) => {
  const auth = await freshSession()
  let calls = 0
  t.mock.method(globalThis, 'fetch', async () => { calls += 1; return json(identity) })
  await assert.rejects(auth.authFetch('https://untrusted.test/key.pem'), /same-origin/)
  assert.equal(calls, 0)
})

test('login errors remain unauthenticated and display useful messages', async (t) => {
  const auth = await freshSession()
  t.mock.method(globalThis, 'fetch', async () => json({}, 429))
  await assert.rejects(auth.signIn('operator', 'wrong'), /Too many attempts/)
  assert.equal(auth.getSession(), undefined)
})

test('session service failures remain visible instead of silently treating them as logout', async (t) => {
  const auth = await freshSession()
  t.mock.method(globalThis, 'fetch', async () => json({}, 503))
  await assert.rejects(auth.restoreSession(), /Unable to check your session/)
  assert.equal(auth.getSession(), undefined)
})

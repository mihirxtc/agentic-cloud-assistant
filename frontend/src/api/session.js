// Session and CSRF state stay in memory. The session cookie is HttpOnly.
let session = undefined
let revision = 0
let restorePromise = null
const listeners = new Set()

export function getSession() { return session }
export function subscribeSession(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
function updateSession(value) {
  session = value
  revision += 1
  listeners.forEach((listener) => listener())
}

export function clearSession() { updateSession(null) }

export async function authFetch(path, options = {}) {
  const origin = globalThis.location?.origin ?? 'http://localhost'
  const url = new URL(path, origin)
  if (url.origin !== origin) throw new Error('Use the same-origin backend proxy')
  const headers = new Headers(options.headers)
  const method = (options.method ?? 'GET').toUpperCase()
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && session?.csrf_token) {
    headers.set('X-CSRF-Token', session.csrf_token)
  }
  const requestRevision = revision
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin', redirect: 'error' })
  if (response.status === 401 && url.pathname !== '/auth/login' && revision === requestRevision) clearSession()
  return response
}

export async function restoreSession() {
  if (restorePromise) return restorePromise
  const expectedRevision = revision
  restorePromise = (async () => {
    const response = await authFetch('/auth/session')
    if (response.status === 401) return null
    if (!response.ok) throw new Error('Unable to check your session. Try again.')
    const result = await response.json()
    if (revision === expectedRevision) updateSession(result)
    return result
  })().finally(() => { restorePromise = null })
  return restorePromise
}

export async function signIn(username, password) {
  const response = await authFetch('/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ACA-Client': 'browser' },
    body: JSON.stringify({ username, password }),
  })
  if (!response.ok) {
    const messages = { 401: 'Invalid credentials', 429: 'Too many attempts. Please try again later.',
      503: 'Server login is not configured. Contact the operator.' }
    throw new Error(messages[response.status] ?? 'Sign-in failed. Please try again.')
  }
  const result = await response.json()
  updateSession(result)
  return result
}

export async function signOut() {
  const response = await authFetch('/auth/logout', { method: 'POST' })
  if (!response.ok && response.status !== 401) throw new Error('Sign-out failed. Please try again.')
  clearSession()
}

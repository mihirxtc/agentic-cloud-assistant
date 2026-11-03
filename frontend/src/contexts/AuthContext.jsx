import { useEffect, useState, useSyncExternalStore } from 'react'
import { AuthContext } from './useAuth'
import { getSession, restoreSession, signIn, signOut, subscribeSession } from '../api/session'

export function AuthProvider({ children }) {
  const session = useSyncExternalStore(subscribeSession, getSession)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    restoreSession().catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])

  async function retry() {
    setError(null)
    try { await restoreSession() } catch (err) { setError(err.message) }
  }

  return (
    <AuthContext.Provider value={{
      isAuthenticated: !!session?.authenticated, checkingSession: session === undefined,
      sessionError: error, retrySession: retry, login: signIn, logout: signOut,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

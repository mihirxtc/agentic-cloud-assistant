import { createContext, useContext, useState } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    try { return localStorage.getItem('aca_auth') === 'true' } catch { return false }
  })

  function login(username, password) {
    if (username === 'admin' && password === 'mscProject@DMU') {
      setIsAuthenticated(true)
      try { localStorage.setItem('aca_auth', 'true') } catch {}
      return true
    }
    return false
  }

  function logout() {
    setIsAuthenticated(false)
    try { localStorage.clear() } catch {}
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}

import { createContext, useContext, useState, useEffect } from 'react'

export const DEFAULT_KEYS = {
  model:          'groq',
  region:         'us-east-1',
  groq_key:       '',
  anthropic_key:  '',
  ollama_url:     '',
  aws_access_key: '',
  aws_secret_key: '',
}

const ApiKeyContext = createContext(null)

export function ApiKeyProvider({ children }) {
  const [keys, setKeys] = useState(() => {
    try {
      const stored = localStorage.getItem('agentic_cloud_api_keys')
      return stored ? { ...DEFAULT_KEYS, ...JSON.parse(stored) } : DEFAULT_KEYS
    } catch { return DEFAULT_KEYS }
  })

  useEffect(() => {
    try { localStorage.setItem('agentic_cloud_api_keys', JSON.stringify(keys)) } catch {}
  }, [keys])

  function setAllKeys(newKeys) {
    setKeys({ ...DEFAULT_KEYS, ...newKeys })
  }

  function clearAllKeys() {
    setKeys(DEFAULT_KEYS)
    try { localStorage.removeItem('agentic_cloud_api_keys') } catch {}
  }

  return (
    <ApiKeyContext.Provider value={{ keys, setAllKeys, clearAllKeys }}>
      {children}
    </ApiKeyContext.Provider>
  )
}

export function useApiKeys() {
  return useContext(ApiKeyContext)
}

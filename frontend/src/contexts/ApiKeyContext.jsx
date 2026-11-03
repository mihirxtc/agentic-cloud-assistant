import { useState, useEffect, useSyncExternalStore } from 'react'
import { ApiKeyContext } from './useApiKeys'
import { useAuth } from './useAuth'
import { authFetch } from '../api/session'
import { getAwsState, subscribeAwsState, refreshAwsIdentity, clearAwsIdentity } from '../api/awsIdentity'
import { DEFAULT_PREFERENCES, browserStorage, readPreferences, savePreferences } from '../utils/preferences'

export function ApiKeyProvider({ children }) {
  const { isAuthenticated } = useAuth()
  const [keys, setKeys] = useState(() => readPreferences(browserStorage()))
  const [serverConfig, setServerConfig] = useState(null)
  const [configError, setConfigError] = useState(null)
  const awsState = useSyncExternalStore(subscribeAwsState, getAwsState)

  useEffect(() => {
    if (isAuthenticated) refreshAwsIdentity(keys.region)
    else clearAwsIdentity()
    return () => clearAwsIdentity()
  }, [isAuthenticated, keys.region])

  useEffect(() => {
    savePreferences(browserStorage(), keys)
  }, [keys])

  useEffect(() => {
    if (!isAuthenticated) return
    let active = true
    authFetch('/auth/config').then(async (response) => {
      if (!response.ok) throw new Error('Unable to load server configuration status')
      const config = await response.json()
      if (active) { setServerConfig(config); setConfigError(null) }
    }).catch((err) => { if (active) setConfigError(err.message) })
    return () => { active = false }
  }, [isAuthenticated])

  function setAllKeys(preferences) { setKeys(savePreferences(browserStorage(), preferences)) }
  function clearAllKeys() { setKeys({ ...DEFAULT_PREFERENCES }) }

  return (
    <ApiKeyContext.Provider value={{ keys, setAllKeys, clearAllKeys,
      serverConfig: isAuthenticated ? serverConfig : null, configError,
      awsIdentity: isAuthenticated && awsState.region === keys.region ? awsState.identity : null,
      awsIdentityError: awsState.region === keys.region ? awsState.error : null,
      awsIdentityLoading: awsState.loading, refreshAwsIdentity: () => refreshAwsIdentity(keys.region) }}>
      {children}
    </ApiKeyContext.Provider>
  )
}

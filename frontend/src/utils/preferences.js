export function browserStorage() {
  try { return globalThis.localStorage } catch { return undefined }
}

export const DEFAULT_PREFERENCES = { model: 'groq', region: 'us-east-1' }

export function sanitizePreferences(value) {
  return {
    model: ['groq', 'anthropic', 'ollama'].includes(value?.model) ? value.model : DEFAULT_PREFERENCES.model,
    region: ['us-east-1', 'eu-west-1', 'ap-southeast-1', 'ap-southeast-2'].includes(value?.region)
      ? value.region : DEFAULT_PREFERENCES.region,
  }
}

export function readPreferences(storage) {
  try {
    const raw = storage.getItem('aca_preferences') ?? storage.getItem('agentic_cloud_api_keys')
    // Purge old secrets and the old browser-only login flag, even for bad JSON.
    storage.removeItem('agentic_cloud_api_keys')
    storage.removeItem('aca_auth')
    return sanitizePreferences(raw ? JSON.parse(raw) : null)
  } catch { return { ...DEFAULT_PREFERENCES } }
}

export function savePreferences(storage, value) {
  const preferences = sanitizePreferences(value)
  try { storage.setItem('aca_preferences', JSON.stringify(preferences)) } catch { /* Storage is optional. */ }
  return preferences
}

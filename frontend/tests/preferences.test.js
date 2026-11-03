import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_PREFERENCES, readPreferences, savePreferences } from '../src/utils/preferences.js'

function storageWith(values) {
  const entries = new Map(Object.entries(values))
  return { entries, getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value), removeItem: (key) => entries.delete(key) }
}

test('legacy keys and the login flag are removed; only safe preferences migrate', () => {
  const storage = storageWith({ aca_auth: 'true', agentic_cloud_api_keys: JSON.stringify({
    model: 'anthropic', region: 'eu-west-1', aws_secret_key: 'test-secret', groq_key: 'test-secret',
  }) })
  const preferences = readPreferences(storage)
  assert.deepEqual(preferences, { model: 'anthropic', region: 'eu-west-1' })
  assert.equal(storage.entries.has('aca_auth'), false)
  assert.equal(storage.entries.has('agentic_cloud_api_keys'), false)
  savePreferences(storage, { ...preferences, aws_secret_key: 'test-secret' })
  assert.equal(storage.getItem('aca_preferences').includes('test-secret'), false)
})

test('malformed legacy data is purged too', () => {
  const storage = storageWith({ aca_auth: 'true', agentic_cloud_api_keys: 'broken json' })
  assert.deepEqual(readPreferences(storage), DEFAULT_PREFERENCES)
  assert.equal(storage.entries.size, 0)
})

test('unsupported fields and values never enter persisted preferences', () => {
  const storage = storageWith({})
  assert.deepEqual(savePreferences(storage, { model: 'unknown', region: 'unknown', password: 'secret' }), DEFAULT_PREFERENCES)
  assert.deepEqual(JSON.parse(storage.getItem('aca_preferences')), DEFAULT_PREFERENCES)
})

test('blocked browser storage does not prevent in-memory preferences', () => {
  const storage = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } }
  assert.deepEqual(readPreferences(storage), DEFAULT_PREFERENCES)
  assert.deepEqual(savePreferences(storage, DEFAULT_PREFERENCES), DEFAULT_PREFERENCES)
})

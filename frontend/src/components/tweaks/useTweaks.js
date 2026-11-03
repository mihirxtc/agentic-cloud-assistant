import { useState, useCallback } from 'react'

export function useTweaks(defaults) {
  const [values, setValues] = useState(defaults)
  const setTweak = useCallback((key, val) => {
    setValues((prev) => ({ ...prev, [key]: val }))
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [key]: val } }, '*')
  }, [])
  return [values, setTweak]
}

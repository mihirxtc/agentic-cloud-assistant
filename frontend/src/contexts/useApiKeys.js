import { createContext, useContext } from 'react'

export const ApiKeyContext = createContext(null)

export function useApiKeys() {
  return useContext(ApiKeyContext)
}

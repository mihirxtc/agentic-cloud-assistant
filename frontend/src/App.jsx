import { useState, useEffect } from 'react'
import { Toaster } from 'react-hot-toast'
import { AuthProvider, useAuth }     from './contexts/AuthContext'
import { ApiKeyProvider, useApiKeys } from './contexts/ApiKeyContext'
import { TWEAK_DEFAULTS }            from './utils/constants'
import ErrorBoundary  from './components/ui/ErrorBoundary'
import LoginPage      from './components/LoginPage'
import SettingsModal  from './components/SettingsModal'
import Dashboard      from './components/Dashboard'
import TweaksPanel, {
  useTweaks,
  TweakSection,
  TweakColor,
  TweakSlider,
  TweakRadio,
  TweakSelect,
  TweakToggle,
} from './components/tweaks/TweaksPanel'

function AppInner() {
  const { isAuthenticated }             = useAuth()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [t, setTweak]                   = useTweaks(TWEAK_DEFAULTS)

  useEffect(() => {
    let el = document.getElementById('aca-tweaks')
    if (!el) {
      el = document.createElement('style')
      el.id = 'aca-tweaks'
      document.head.appendChild(el)
    }
    el.textContent = `
      :root {
        --accent-blue:     ${t.accent};
        --accent-blue-dim: ${t.accent}22;
      }
      .aca-panel      { border-radius: ${t.panelRadius}px; }
      .aca-tile, .aca-code { border-radius: ${Math.max(4, t.panelRadius - 2)}px; }
      .aca-panel-body { padding: ${t.density === 'compact' ? 14 : t.density === 'comfy' ? 26 : 20}px; }
      .aca-panel-hd   { padding: ${t.density === 'compact' ? '10px 16px' : t.density === 'comfy' ? '18px 24px' : '14px 20px'}; }
      .mono, .aca-code, .aca-tile-value, .aca-badge, pre, code {
        font-family: '${t.monoFont}', ui-monospace, Menlo, monospace !important;
      }
    `
  }, [t])

  if (!isAuthenticated) return <LoginPage />

  return (
    <>
      <Toaster position="top-right" />
      <Dashboard onOpenSettings={() => setSettingsOpen(true)} />
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Theme" />
        <TweakColor  label="Accent colour" value={t.accent}      onChange={(v) => setTweak('accent', v)} />
        <TweakSlider label="Panel radius"  value={t.panelRadius} min={0} max={16} unit="px"
          onChange={(v) => setTweak('panelRadius', v)} />
        <TweakSection label="Layout" />
        <TweakRadio  label="Density" value={t.density} options={['compact', 'regular', 'comfy']}
          onChange={(v) => setTweak('density', v)} />
        <TweakSection label="Type" />
        <TweakSelect label="Mono font" value={t.monoFont}
          options={['JetBrains Mono', 'Geist Mono', 'IBM Plex Mono', 'Fira Code']}
          onChange={(v) => setTweak('monoFont', v)} />
        <TweakSection label="Chrome" />
        <TweakToggle label="Show security score pill" value={t.showScore}
          onChange={(v) => setTweak('showScore', v)} />
      </TweaksPanel>
    </>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <ApiKeyProvider>
          <AppInner />
        </ApiKeyProvider>
      </AuthProvider>
    </ErrorBoundary>
  )
}

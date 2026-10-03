import { useEffect, useState } from 'react'

function preferredTheme(): boolean {
  try {
    const saved = localStorage.getItem('analyst-theme')
    if (saved === 'dark' || saved === 'light') return saved === 'dark'
  } catch { /* System preference works when local storage is unavailable. */ }
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

interface Props {
  health: 'checking' | 'online' | 'unavailable'
  refreshing: boolean
  onRefresh: () => void
}

export function Header({ health, refreshing, onRefresh }: Props) {
  const [dark, setDark] = useState(preferredTheme)
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }, [dark])

  function toggleTheme() {
    const next = !dark
    setDark(next)
    try { localStorage.setItem('analyst-theme', next ? 'dark' : 'light') } catch { /* Optional preference. */ }
  }

  return (
    <header className="site-header">
      <a href="#main" className="brand" aria-label="Agentic Data Analyst home">
        <span className="brand-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M5 18v-6m7 6V5m7 13v-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" /></svg>
        </span>
        <span>Agentic Data Analyst<span className="brand-caption">A question. A query. A clearer picture.</span></span>
      </a>
      <div className="header-actions">
        <button type="button" className={`api-status ${health}`} onClick={onRefresh} disabled={refreshing}
          aria-label="Refresh backend status and datasets" title="Check API status and reload datasets">
          <span className="status-dot" aria-hidden="true" />
          {health === 'online' ? 'Demo API online' : health === 'checking' ? 'Checking demo API' : 'Demo API unavailable'}
          <span aria-hidden="true">↻</span>
        </button>
        <button type="button" className="theme-button" onClick={toggleTheme}
          aria-label={`Switch to ${dark ? 'light' : 'dark'} mode`} title={`Switch to ${dark ? 'light' : 'dark'} mode`}>
          {dark ? '☀' : '☾'}
        </button>
      </div>
    </header>
  )
}

import { useEffect, useState } from 'react'
import { ApiError, getDatabases, getHealth, queryAgent } from './api/client'
import type { Dataset, QueryRequest } from './api/types'
import { Header } from './components/Header'
import { DatasetSelector } from './components/DatasetSelector'
import { QuestionForm } from './components/QuestionForm'
import { ExampleQuestions } from './components/ExampleQuestions'
import { AnswerCard, type CompletedQuery } from './components/AnswerCard'
import { exampleQuestions } from './examples'

export default function App() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [selected, setSelected] = useState('')
  const [question, setQuestion] = useState('')
  const [discovering, setDiscovering] = useState(true)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [health, setHealth] = useState<'checking' | 'online' | 'unavailable'>('checking')
  const [busy, setBusy] = useState(false)
  const [slow, setSlow] = useState(false)
  const [result, setResult] = useState<CompletedQuery | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastRequest, setLastRequest] = useState<QueryRequest | null>(null)

  async function discover() {
    setDiscovering(true)
    setDiscoveryError(null)
    setHealth('checking')
    const [profiles, status] = await Promise.allSettled([getDatabases(), getHealth()])
    if (profiles.status === 'fulfilled') {
      setDatasets(profiles.value)
      setSelected(previous => profiles.value.some(item => item.id === previous) ? previous : profiles.value[0].id)
    } else {
      setDiscoveryError('We couldn’t load the demo datasets. Check that the API is available, then retry.')
    }
    setHealth(status.status === 'fulfilled' && status.value ? 'online' : 'unavailable')
    setDiscovering(false)
  }

  useEffect(() => { void discover() }, [])
  useEffect(() => {
    if (!busy) { setSlow(false); return }
    const timer = window.setTimeout(() => setSlow(true), 10000)
    return () => window.clearTimeout(timer)
  }, [busy])

  async function submit(request: QueryRequest) {
    if (busy || !request.question.trim() || request.question.length > 4000) return
    setBusy(true)
    setResult(null)
    setError(null)
    setLastRequest(request)
    const datasetName = datasets.find(item => item.id === request.database)?.name || request.database
    try {
      const answer = await queryAgent(request)
      setResult({ answer, question: request.question, datasetName })
      setHealth('online')
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : 'The request could not be completed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = !!selected && !!question.trim() && question.length <= 4000 && !busy && !discovering

  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">Skip to workspace</a>
      <Header health={health} refreshing={discovering || busy} onRefresh={() => void discover()} />
      <main id="main" className="page-main" tabIndex={-1}>
        <section className="hero" aria-labelledby="hero-heading">
          <div>
            <div className="eyebrow"><span className="status-dot" aria-hidden="true" /> A DATA ANALYST, ON DEMAND</div>
            <h1 id="hero-heading">Ask your data.<br /><span>Get a clear answer.</span></h1>
            <p className="hero-description">Ask questions about structured data in plain English. The agent discovers the schema, writes safe read-only SQL, and explains what it finds.</p>
            <div className="technology-list" aria-label="Built with">
              {['Gemini', 'LangChain', 'FastAPI', 'PostgreSQL'].map(name => <span key={name}>{name}</span>)}
              <span className="read-only-badge">Read-only SQL</span>
            </div>
          </div>
          <aside className="workflow-card" aria-label="How the agent works">
            <span className="workflow-caption">FROM QUESTION TO CLARITY</span>
            <ol>
              <li><span className="step-index">01</span><div><strong>Discover the schema</strong><span>Understand tables and relationships</span></div></li>
              <li><span className="step-index">02</span><div><strong>Query the data</strong><span>Generate and execute read-only SQL</span></div></li>
              <li><span className="step-index">03</span><div><strong>Explain the result</strong><span>An answer grounded in real records</span></div></li>
            </ol>
          </aside>
        </section>

        <div className="workspace-heading"><h2>Explore a dataset</h2><span>Two synthetic datasets. One curious mind.</span></div>
        <div className="workspace-grid">
          <section className="query-panel" aria-label="Query workspace">
            {discovering && !datasets.length ? <p className="discovery-status" role="status">Loading demo datasets…</p> : null}
            {discoveryError && <div className="discovery-error" role="alert"><p>{discoveryError}</p>
              <button className="secondary-button" type="button" disabled={discovering} onClick={() => void discover()}>Retry connection</button></div>}
            {!!datasets.length && <DatasetSelector datasets={datasets} selected={selected} disabled={busy || discovering} onSelect={setSelected} />}
            <QuestionForm question={question} busy={busy} canSubmit={canSubmit} onChange={setQuestion}
              onSubmit={() => void submit({ question: question.trim(), database: selected })} />
            <ExampleQuestions questions={exampleQuestions[selected] || []} disabled={busy || discovering} onChoose={setQuestion} />
          </section>
          <AnswerCard result={result} busy={busy} slow={slow} error={error}
            onRetry={() => { if (lastRequest) void submit(lastRequest) }} />
        </div>
        <footer className="page-footer"><span>Built to turn questions into understanding.</span><span>A developer portfolio demo · No personal data</span></footer>
      </main>
    </div>
  )
}

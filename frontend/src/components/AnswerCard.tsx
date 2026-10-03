import type { PublicAnswer } from '../api/types'

export interface CompletedQuery {
  answer: PublicAnswer
  question: string
  datasetName: string
}

interface Props {
  result: CompletedQuery | null
  busy: boolean
  slow: boolean
  error: string | null
  onRetry: () => void
}

export function AnswerCard({ result, busy, slow, error, onRetry }: Props) {
  return (
    <section className="answer-panel" aria-labelledby="answer-heading" aria-busy={busy}>
      <div className="answer-header"><h2 id="answer-heading">Your answer</h2>
        {result && !busy && !error && <span className="success-badge"><span aria-hidden="true">✓</span> Completed</span>}
      </div>
      <div aria-live="polite" aria-atomic="true" className="answer-content">
        {busy ? (
          <div className="answer-placeholder" role="status">
            <span className="loading-orbit" aria-hidden="true"><span className="spinner" /></span>
            <h3>The agent is working…</h3>
            <p>Waiting for an answer grounded in your selected dataset.</p>
            {slow && <p className="slow-message">The demo server may be waking up. The first request can take longer.</p>}
          </div>
        ) : error ? (
          <div className="answer-placeholder error-state" role="alert">
            <span className="placeholder-icon" aria-hidden="true">!</span>
            <h3>We couldn’t complete that request</h3>
            <p>{error}</p>
            <button type="button" className="secondary-button" onClick={onRetry}>Retry question <span aria-hidden="true">↻</span></button>
          </div>
        ) : result ? (
          <>
            <div className="result-dataset">{result.datasetName}</div>
            <div className="result-question"><h3>Question</h3><p>{result.question}</p></div>
            <div className="result-answer"><h3>Answer</h3><p>{result.answer.answer}</p></div>
            <details className="technical-details">
              <summary>Run details</summary>
              <dl>
                <div><dt>Dataset</dt><dd>{result.datasetName}</dd></div>
                <div><dt>Status</dt><dd>Completed</dd></div>
                <div><dt>Execution</dt><dd>Read-only SQL agent</dd></div>
                <div><dt>Run ID</dt><dd className="run-id">{result.answer.run_id}</dd></div>
              </dl>
            </details>
          </>
        ) : (
          <div className="answer-placeholder">
            <span className="placeholder-icon" aria-hidden="true">↗</span>
            <h3>A little curiosity goes a long way.</h3>
            <p>Pick a dataset and ask your first question. Your answer will appear here.</p>
            <span className="placeholder-caption">Real queries. Clear explanations.</span>
          </div>
        )}
      </div>
      <div className="answer-footer"><span className="status-dot" aria-hidden="true" /> Synthetic data only · Read-only access</div>
    </section>
  )
}

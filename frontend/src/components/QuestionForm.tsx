import type { FormEvent } from 'react'

interface Props {
  question: string
  busy: boolean
  canSubmit: boolean
  onChange: (value: string) => void
  onSubmit: () => void
}

export function QuestionForm({ question, busy, canSubmit, onChange, onSubmit }: Props) {
  function submit(event: FormEvent) {
    event.preventDefault()
    if (canSubmit) onSubmit()
  }

  return (
    <form onSubmit={submit} className="question-form" aria-busy={busy}>
      <label htmlFor="question" className="section-label">02 <span>Ask a question</span></label>
      <textarea id="question" name="question" rows={5} maxLength={4000} value={question}
        disabled={busy} onChange={event => onChange(event.target.value)}
        placeholder="What would you like to learn from this data?"
        aria-describedby="question-help question-count"
        onKeyDown={event => {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
            event.preventDefault()
            if (canSubmit) event.currentTarget.form?.requestSubmit()
          }
        }} />
      <div className="input-meta">
        <span id="question-help">Be specific. Plain English is enough.</span>
        <span id="question-count">{question.length.toLocaleString()} / 4,000</span>
      </div>
      <div className="submit-row">
        <span className="keyboard-hint">Ctrl / ⌘ + Enter to ask</span>
        <button className="primary-button" type="submit" disabled={!canSubmit}>
          {busy ? <><span className="spinner" aria-hidden="true" /> Agent is working</> : <>Ask Agent <span aria-hidden="true">↗</span></>}
        </button>
      </div>
    </form>
  )
}

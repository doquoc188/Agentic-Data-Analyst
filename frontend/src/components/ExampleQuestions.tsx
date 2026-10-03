interface Props {
  questions: string[]
  disabled: boolean
  onChoose: (question: string) => void
}

export function ExampleQuestions({ questions, disabled, onChoose }: Props) {
  if (!questions.length) return null
  return (
    <section className="example-section" aria-labelledby="example-heading">
      <h3 id="example-heading">Need a starting point?</h3>
      <div className="example-list">
        {questions.map(question => (
          <button key={question} type="button" disabled={disabled} onClick={() => onChoose(question)}>
            <span>{question}</span><span aria-hidden="true">↗</span>
          </button>
        ))}
      </div>
    </section>
  )
}

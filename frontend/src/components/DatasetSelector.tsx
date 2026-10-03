import type { Dataset } from '../api/types'

interface Props {
  datasets: Dataset[]
  selected: string
  disabled: boolean
  onSelect: (id: string) => void
}

export function DatasetSelector({ datasets, selected, disabled, onSelect }: Props) {
  return (
    <fieldset className="dataset-fieldset" disabled={disabled}>
      <legend className="section-label">01 <span>Choose your dataset</span></legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {datasets.map(dataset => (
          <label key={dataset.id} className={`dataset-option ${selected === dataset.id ? 'selected' : ''}`}>
            <input className="sr-only" type="radio" name="dataset" value={dataset.id}
              checked={selected === dataset.id} onChange={() => onSelect(dataset.id)} />
            <div className="dataset-topline">
              <span className="dataset-symbol" aria-hidden="true">{dataset.id === 'sales' ? '▥' : '▦'}</span>
              <span className="radio-indicator" aria-hidden="true">{selected === dataset.id ? '✓' : ''}</span>
            </div>
            <span className="dataset-name">{dataset.name}</span>
            <span className="dataset-description">{dataset.description}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}

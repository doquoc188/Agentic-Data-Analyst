import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import App from './App'

const datasets = [
  { id: 'sales', name: 'Retail sample from API', description: 'A retail dataset supplied by the server' },
  { id: 'saas', name: 'Subscriptions from API', description: 'A subscription dataset supplied by the server' },
]
const success = {
  status: 'success', answer: 'The queried dataset contains 300 records.', database: 'sales',
  run_id: 'ba57c201-cc47-493e-8c5c-c4b774993d27', trace_path: 'D:\\server\\private\\run.json',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

type FetchMock = Mock<(url: string, options?: RequestInit) => Promise<Response>>
let post: FetchMock
let fetchMock: FetchMock

beforeEach(() => {
  post = vi.fn(async () => json(success))
  fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/databases')) return json(datasets)
    if (url.endsWith('/health')) return json({ status: 'ok' })
    if (url.endsWith('/query')) return post(url, options)
    throw new Error('Unexpected mocked endpoint')
  })
  vi.stubGlobal('fetch', fetchMock)
})

async function openPage() {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByRole('radio', { name: /Retail sample from API/ })
  await waitFor(() => expect(screen.getByRole('radio', { name: /Retail sample/ })).toBeEnabled())
  return user
}

describe('public analyst workspace', () => {
  it('renders the main page and discovers dataset metadata from the API', async () => {
    await openPage()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Ask your data.')
    expect(screen.getByRole('link', { name: 'Agentic Data Analyst home' })).toBeVisible()
    expect(screen.getByText('A subscription dataset supplied by the server')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Refresh backend status and datasets' })).toHaveTextContent('Demo API online')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(post).not.toHaveBeenCalled()
  })

  it('selects datasets with native radio controls and updates example suggestions', async () => {
    const user = await openPage()
    await user.click(screen.getByRole('radio', { name: /Subscriptions from API/ }))
    expect(screen.getByRole('radio', { name: /Subscriptions from API/ })).toBeChecked()
    expect(screen.getByRole('button', { name: 'How many unresolved high-priority support tickets are there?' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Which city/ })).not.toBeInTheDocument()
  })

  it('populates an example without automatically submitting it', async () => {
    const user = await openPage()
    const example = 'Which city generated the most revenue from completed orders?'
    await user.click(screen.getByRole('button', { name: example }))
    expect(screen.getByRole('textbox', { name: /Ask a question/ })).toHaveValue(example)
    expect(post).not.toHaveBeenCalled()
  })

  it('rejects empty and whitespace-only questions and respects the character limit', async () => {
    const user = await openPage()
    const input = screen.getByRole('textbox', { name: /Ask a question/ })
    const submit = screen.getByRole('button', { name: /Ask Agent/ })
    expect(submit).toBeDisabled()
    await user.type(input, '   ')
    expect(submit).toBeDisabled()
    expect(input).toHaveAttribute('maxlength', '4000')
    fireEvent.change(input, { target: { value: 'x'.repeat(4001) } })
    expect(submit).toBeDisabled()
    expect(post).not.toHaveBeenCalled()
  })

  it('submits the selected profile and trimmed question, then displays the answer', async () => {
    const user = await openPage()
    post.mockResolvedValueOnce(json({ ...success, database: 'saas' }))
    await user.click(screen.getByRole('radio', { name: /Subscriptions from API/ }))
    await user.type(screen.getByRole('textbox', { name: /Ask a question/ }), '  Count subscriptions  ')
    await user.click(screen.getByRole('button', { name: /Ask Agent/ }))
    expect(await screen.findByText(success.answer)).toBeVisible()
    const options = post.mock.calls[0][1] as RequestInit
    expect(JSON.parse(options.body as string)).toEqual({ question: 'Count subscriptions', database: 'saas' })
    expect(options.credentials).toBe('omit')
    expect(screen.getByText('Count subscriptions', { selector: 'p' })).toBeVisible()
    expect(screen.getByText('Completed', { selector: '.success-badge' })).toBeVisible()
    expect(screen.getByText('Subscriptions from API', { selector: '.result-dataset' })).toBeVisible()
    expect(document.body.innerHTML).not.toContain(success.trace_path)
    expect(document.body.innerHTML).not.toContain('server\\private')
    await user.click(screen.getByText('Run details'))
    expect(screen.getByText(success.run_id)).toBeVisible()
    expect(screen.queryByRole('link', { name: /trace/i })).not.toBeInTheDocument()
  })

  it('Enter adds a newline; Ctrl+Enter submits once', async () => {
    const user = await openPage()
    const input = screen.getByRole('textbox', { name: /Ask a question/ })
    await user.type(input, 'Count records{Enter}')
    expect(input).toHaveValue('Count records\n')
    expect(post).not.toHaveBeenCalled()
    await user.keyboard('{Control>}{Enter}{/Control}')
    await screen.findByText(success.answer)
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('shows honest loading feedback and disables duplicate requests', async () => {
    await openPage()
    post.mockReturnValueOnce(new Promise(() => {}))
    vi.useFakeTimers()
    fireEvent.change(screen.getByRole('textbox', { name: /Ask a question/ }), { target: { value: 'Count' } })
    fireEvent.click(screen.getByRole('button', { name: /Ask Agent/ }))
    expect(screen.getByRole('status')).toHaveTextContent('The agent is working…')
    expect(screen.getByRole('button', { name: /Agent is working/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /Subscriptions from API/ })).toBeDisabled()
    act(() => { vi.advanceTimersByTime(10000) })
    expect(screen.getByText(/The demo server may be waking up/)).toBeVisible()
    expect(post).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it.each([400, 500, 503])('handles HTTP %i without exposing raw diagnostics or secrets', async status => {
    const user = await openPage()
    post.mockResolvedValueOnce(json({ status: 'error', message: 'GOOGLE_API_KEY=secret-value',
      trace_path: 'D:\\internal\\private.json', database_url: 'postgresql://user:secret@host/db' }, status))
    await user.type(screen.getByRole('textbox', { name: /Ask a question/ }), 'Count records')
    await user.click(screen.getByRole('button', { name: /Ask Agent/ }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We couldn’t complete that request')
    if (status === 503) expect(alert).toHaveTextContent('temporarily unavailable')
    expect(document.body.textContent).not.toContain('secret-value')
    expect(document.body.textContent).not.toContain('postgresql://')
    expect(document.body.textContent).not.toContain('private.json')
    expect(screen.getByRole('button', { name: /Retry question/ })).toBeEnabled()
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('retries only on a user action and resends the original question/profile', async () => {
    const user = await openPage()
    post.mockResolvedValueOnce(json({ message: 'Unavailable' }, 503))
    await user.type(screen.getByRole('textbox', { name: /Ask a question/ }), 'Count records')
    await user.click(screen.getByRole('button', { name: /Ask Agent/ }))
    await screen.findByRole('alert')
    expect(post).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /Retry question/ }))
    await screen.findByText(success.answer)
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[1][1]?.body).toEqual(post.mock.calls[0][1]?.body)
  })

  it('recovers dataset discovery through a manual connection retry', async () => {
    fetchMock.mockImplementationOnce(async () => { throw new Error('raw network diagnostics') })
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('We couldn’t load the demo datasets')
    expect(screen.getByRole('button', { name: /Ask Agent/ })).toBeDisabled()
    expect(document.body.textContent).not.toContain('raw network diagnostics')
    await user.click(screen.getByRole('button', { name: 'Retry connection' }))
    await screen.findByRole('radio', { name: /Retail sample/ })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not block queries when the optional health check fails', async () => {
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.endsWith('/databases')) return json(datasets)
      if (url.endsWith('/health')) throw new Error('Health check unavailable')
      return post(url, options)
    })
    const user = await openPage()
    expect(screen.getByRole('button', { name: 'Refresh backend status and datasets' })).toHaveTextContent('Demo API unavailable')
    await user.type(screen.getByRole('textbox', { name: /Ask a question/ }), 'Count')
    expect(screen.getByRole('button', { name: /Ask Agent/ })).toBeEnabled()
  })

  it('supports keyboard-accessible controls and theme preference without an account', async () => {
    const user = await openPage()
    expect(screen.getByRole('link', { name: 'Skip to workspace' })).toHaveAttribute('href', '#main')
    expect(screen.getByRole('group', { name: /Choose your dataset/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Ask a question/ })).toHaveAccessibleDescription(/Plain English/)
    await user.click(screen.getByRole('button', { name: 'Switch to dark mode' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('analyst-theme')).toBe('dark')
    await user.click(screen.getByRole('button', { name: 'Switch to light mode' }))
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('starts with the system dark preference when no saved preference exists', async () => {
    vi.mocked(window.matchMedia).mockReturnValueOnce({ matches: true } as MediaQueryList)
    await openPage()
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})

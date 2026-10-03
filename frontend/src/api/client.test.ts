import { describe, expect, it, vi } from 'vitest'

describe('typed public API client', () => {
  it('reads the configurable API base URL and strips a trailing slash', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://demo-api.example.invalid/')
    vi.resetModules()
    const client = await import('./client')
    const fetch = vi.fn(async () => new Response(JSON.stringify([{ id: 'sales', name: 'Sales', description: 'Synthetic' }]), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await client.getDatabases()
    expect(fetch).toHaveBeenCalledWith('https://demo-api.example.invalid/databases', expect.objectContaining({ credentials: 'omit' }))
  })

  it('discards local trace paths and extra fields before results reach React', async () => {
    const client = await import('./client')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      status: 'success', answer: 'A real answer', run_id: 'run-id', database: 'sales',
      trace_path: 'D:\\private\\trace.json', internal: 'private-data',
    }), { status: 200 })))
    expect(await client.queryAgent({ question: 'Count', database: 'sales' })).toEqual({
      status: 'success', answer: 'A real answer', run_id: 'run-id', database: 'sales',
    })
  })

  it('does not read failed response bodies or expose a raw network exception', async () => {
    const client = await import('./client')
    const body = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: body })))
    await expect(client.getDatabases()).rejects.toThrow('temporarily unavailable')
    expect(body).not.toHaveBeenCalled()
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Authorization: secret') }))
    await expect(client.getDatabases()).rejects.toThrow('Unable to reach the demo API')
  })

  it('rejects malformed successful responses with fixed public feedback', async () => {
    const client = await import('./client')
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{not-json secret-token', { status: 200 })))
    await expect(client.getHealth()).rejects.toThrow('unreadable response')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ answer: 'incomplete' }), { status: 200 })))
    await expect(client.queryAgent({ question: 'Count', database: 'sales' })).rejects.toThrow('incomplete answer')
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })))
    await expect(client.getDatabases()).rejects.toThrow('Dataset information is unavailable')
  })

  it('bounds a stalled startup check and leaves retries to the user', async () => {
    const client = await import('./client')
    vi.useFakeTimers()
    const fetch = vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetch)
    const check = expect(client.getHealth()).rejects.toThrow('API check timed out')
    await vi.advanceTimersByTimeAsync(15000)
    await check
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

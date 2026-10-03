import type { Dataset, PublicAnswer, QueryRequest, QuerySuccess } from './types'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

function publicError(status: number): string {
  if (status === 400) return 'Check your question and dataset, then try again.'
  if (status === 503) return 'The demo service is temporarily unavailable. Please try again in a moment.'
  return 'The service could not complete this request. Please try again.'
}

async function request(path: string, options: RequestInit = {}, timeoutMs?: number): Promise<unknown> {
  const controller = new AbortController()
  const timer = timeoutMs ? window.setTimeout(() => controller.abort(), timeoutMs) : undefined
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options, credentials: 'omit', signal: controller.signal,
    })
    // Never display or log error bodies: even a proxy can return raw diagnostics.
    if (!response.ok) throw new ApiError(response.status, publicError(response.status))
    try {
      return await response.json()
    } catch {
      if (controller.signal.aborted) throw new ApiError(0, 'The API check timed out. Please retry the connection.')
      throw new ApiError(502, 'The service returned an unreadable response. Please try again.')
    }
  } catch (failure) {
    if (failure instanceof ApiError) throw failure
    if (controller.signal.aborted) throw new ApiError(0, 'The API check timed out. Please retry the connection.')
    throw new ApiError(0, 'Unable to reach the demo API. Check your connection and try again.')
  } finally {
    window.clearTimeout(timer)
  }
}

export async function getDatabases(): Promise<Dataset[]> {
  const data = await request('/databases', {}, 15000)
  if (!Array.isArray(data) || !data.length || !data.every(item =>
    item && typeof item.id === 'string' && typeof item.name === 'string' && typeof item.description === 'string',
  )) throw new ApiError(502, 'Dataset information is unavailable. Please try again.')
  return data.map(({ id, name, description }) => ({ id, name, description }))
}

export async function getHealth(): Promise<boolean> {
  const data = await request('/health', {}, 15000)
  return !!data && typeof data === 'object' && 'status' in data && data.status === 'ok'
}

export async function queryAgent(question: QueryRequest): Promise<PublicAnswer> {
  const data = await request('/query', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(question),
  }) as Partial<QuerySuccess> | null
  if (!data || data.status !== 'success' || typeof data.answer !== 'string'
      || typeof data.run_id !== 'string' || typeof data.database !== 'string') {
    throw new ApiError(502, 'The service returned an incomplete answer. Please try again.')
  }
  return { status: 'success', answer: data.answer, run_id: data.run_id, database: data.database }
}

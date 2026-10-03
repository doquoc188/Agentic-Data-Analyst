export interface Dataset {
  id: string
  name: string
  description: string
}

export interface QueryRequest {
  question: string
  database: string
}

// Matches the server contract. Filesystem paths are deliberately discarded
// by the client before results reach application components.
export interface QuerySuccess {
  status: 'success'
  answer: string
  run_id: string
  trace_path: string | null
  database: string
}

export interface QueryError {
  status: 'error'
  error_type: string
  message: string
  run_id: string | null
  trace_path: string | null
}

export type PublicAnswer = Omit<QuerySuccess, 'trace_path'>

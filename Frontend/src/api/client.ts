import type { AxiosError } from 'axios'
import axios, { type AxiosInstance, type AxiosRequestConfig } from 'axios'
import type { APIError, ActionResult } from '@/types'

// Base URL is handled by Vite proxy in development
const BASE_URL = ''

export class APIClient {
  protected readonly client: AxiosInstance

  constructor(baseURL = BASE_URL) {
    this.client = axios.create({
      baseURL,
      timeout: 10_000,
      headers: {
        'Content-Type': 'application/json',
      },
    })

    // Request interceptor for logging
    this.client.interceptors.request.use(
      config => {
        console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`)
        return config
      },
      error => {
        console.error('[API] Request error:', error)
        return Promise.reject(error)
      }
    )

    // Response interceptor for error handling
    this.client.interceptors.response.use(
      response => response,
      (error: AxiosError<APIError>) => {
        if (error.response) {
          // Server responded with error status
          const apiError: APIError = {
            code: error.response.data?.code ?? `HTTP_${error.response.status}`,
            message: error.response.data?.message ?? error.message,
            details: error.response.data?.details,
            request_id: error.response.data?.request_id,
          }
          console.error(`[API] Error ${error.response.status}:`, apiError)
          return Promise.reject(apiError)
        } else if (error.request) {
          // Request made but no response (network error)
          const networkError: APIError = {
            code: 'NETWORK_ERROR',
            message: 'Unable to reach server. Check your connection.',
          }
          console.error('[API] Network error:', networkError)
          return Promise.reject(networkError)
        } else {
          // Something else happened
          const unknownError: APIError = {
            code: 'UNKNOWN_ERROR',
            message: error.message ?? 'An unexpected error occurred.',
          }
          console.error('[API] Unknown error:', unknownError)
          return Promise.reject(unknownError)
        }
      }
    )
  }

  protected async get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.get<T>(url, config)
    return response.data
  }

  protected async post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.post<T>(url, data, config)
    return response.data
  }

  protected async put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.put<T>(url, data, config)
    return response.data
  }

  protected async delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.delete<T>(url, config)
    return response.data
  }
}

// Create and export singleton instance
export const apiClient = new APIClient()

// Helper to check if error is an APIError
export function isAPIError(error: unknown): error is APIError {
  return typeof error === 'object' && error !== null && 'code' in error && 'message' in error
}

// Helper to format error message for display
export function formatAPIError(error: unknown): string {
  if (isAPIError(error)) {
    return `${error.code}: ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'An unexpected error occurred.'
}

// Parse ActionResult from any response
export function parseActionResult(data: unknown): ActionResult {
  if (typeof data === 'object' && data !== null && 'ok' in data) {
    return data as ActionResult
  }
  return { ok: true }
}

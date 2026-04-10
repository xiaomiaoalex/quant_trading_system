import { APIClient } from './client'
import type { BacktestRun, BacktestReport, BacktestRequest } from '@/types'

export class BacktestsAPI extends APIClient {
  async listBacktests(params?: {
    status?: string
    strategy_id?: string
    limit?: number
  }): Promise<BacktestRun[]> {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.strategy_id) searchParams.set('strategy_id', params.strategy_id)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const query = searchParams.toString()
    return this.get<BacktestRun[]>(`/v1/backtests${query ? `?${query}` : ''}`)
  }

  async getBacktest(runId: string): Promise<BacktestRun> {
    return this.get<BacktestRun>(`/v1/backtests/${runId}`)
  }

  async createBacktest(request: BacktestRequest): Promise<BacktestRun> {
    return this.post<BacktestRun>('/v1/backtests', request)
  }

  async getBacktestReport(runId: string): Promise<BacktestReport> {
    return this.get<BacktestReport>(`/v1/backtests/${runId}/report`)
  }
}

export const backtestsAPI = new BacktestsAPI()
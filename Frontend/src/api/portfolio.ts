import { APIClient } from './client'

export class PortfolioAPI extends APIClient {
  async getStrategyPositions(params?: {
    strategy_id?: string
    symbol?: string
  }): Promise<unknown> {
    return this.get<unknown>('/v1/portfolio/strategy-positions', { params })
  }

  async getPositionBreakdown(symbol: string): Promise<unknown> {
    return this.get<unknown>(`/v1/portfolio/positions/${symbol}/breakdown`)
  }

  async getPositionLots(symbol: string, strategy_id: string): Promise<unknown> {
    return this.get<unknown>(`/v1/portfolio/positions/${symbol}/lots`, { params: { strategy_id } })
  }
}

export const portfolioAPI = new PortfolioAPI()

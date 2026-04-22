import { APIClient } from './client'
import type {
  RegisteredStrategy,
  StrategyRuntimeInfo,
  StrategyParams,
  StrategyEventEnvelope,
  StrategyCodeVersion,
  StrategyCodeCreateRequest,
  StrategyCodeDebugRequest,
  StrategyCodeDebugResponse,
  TradingPairsResponse,
} from '@/types'
import type { SafetyGateStatus } from '@/types'

interface LoadStrategyPayload {
  module_path?: string
  code?: string
  code_version?: number
  version?: string
  config?: Record<string, unknown>
  symbol?: string
}

export class StrategiesAPI extends APIClient {
  async getRegistry(): Promise<RegisteredStrategy[]> {
    return this.get<RegisteredStrategy[]>('/v1/strategies/registry')
  }

  async getLoaded(): Promise<StrategyRuntimeInfo[]> {
    return this.get<StrategyRuntimeInfo[]>('/v1/strategies/loaded')
  }

  async getStatus(strategyId: string): Promise<StrategyRuntimeInfo> {
    return this.get<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/status`)
  }

  async getParams(strategyId: string): Promise<StrategyParams> {
    return this.get<StrategyParams>(`/v1/strategies/${strategyId}/params`)
  }

  async updateParams(strategyId: string, params: StrategyParams): Promise<{ success: boolean; error?: string }> {
    return this.put<{ success: boolean; error?: string }>(`/v1/strategies/${strategyId}/params`, { config: params })
  }

  async createStrategyCode(request: StrategyCodeCreateRequest): Promise<StrategyCodeVersion> {
    return this.post<StrategyCodeVersion>('/v1/strategies/code', request)
  }

  async getLatestStrategyCode(strategyId: string): Promise<StrategyCodeVersion> {
    return this.get<StrategyCodeVersion>(`/v1/strategies/${strategyId}/code/latest`)
  }

  async getStrategyCodeVersion(strategyId: string, codeVersion: number): Promise<StrategyCodeVersion> {
    return this.get<StrategyCodeVersion>(`/v1/strategies/${strategyId}/code/${codeVersion}`)
  }

  async debugStrategyCode(request: StrategyCodeDebugRequest): Promise<StrategyCodeDebugResponse> {
    return this.post<StrategyCodeDebugResponse>('/v1/strategies/code/debug', request)
  }

  async loadStrategy(strategyId: string, payload: LoadStrategyPayload): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/load`, payload)
  }

  async unloadStrategy(strategyId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/unload`)
  }

  async startStrategy(strategyId: string, symbol = 'BTCUSDT'): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/start`, null, {
      params: { symbol },
    })
  }

  async stopStrategy(strategyId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/stop`)
  }

  async pauseStrategy(strategyId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/pause`)
  }

  async resumeStrategy(strategyId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/resume`)
  }

  async getStrategyEvents(strategyId: string, eventType?: string, limit = 100): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(`/v1/strategies/${strategyId}/events`, {
      params: { event_type: eventType, limit },
    })
  }

  async getStrategySignals(strategyId: string, limit = 50): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(`/v1/strategies/${strategyId}/events/signals`, {
      params: { limit },
    })
  }

  async getStrategyErrors(strategyId: string, limit = 50): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(`/v1/strategies/${strategyId}/events/errors`, {
      params: { limit },
    })
  }

  async getTradingPairs(statusFilter = 'TRADING', quoteAsset = 'USDT'): Promise<TradingPairsResponse> {
    return this.get<TradingPairsResponse>('/v1/exchange/trading-pairs', {
      params: { status_filter: statusFilter, quote_asset: quoteAsset },
    })
  }

  // Safety Gate API
  async getSafetyGateStatus(): Promise<SafetyGateStatus> {
    return this.get<SafetyGateStatus>('/v1/safety-gate/status')
  }

  async setSafetyGateEnabled(enabled: boolean, confirmed = false): Promise<SafetyGateStatus> {
    return this.post<SafetyGateStatus>('/v1/safety-gate/enable', {
      enabled,
      confirmed,
    })
  }
}

export const strategiesAPI = new StrategiesAPI()

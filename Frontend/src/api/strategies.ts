import { APIClient } from './client'
import type { RegisteredStrategy, StrategyRuntimeInfo, StrategyParams } from '@/types'

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

  async loadStrategy(strategyId: string, modulePath: string, version = 'v1', config: Record<string, unknown> = {}): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string; status?: string }>(`/v1/strategies/${strategyId}/load`, {
      module_path: modulePath,
      version,
      config,
    })
  }

  async unloadStrategy(strategyId: string): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string }>(`/v1/strategies/${strategyId}/unload`)
  }

  async startStrategy(strategyId: string): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string }>(`/v1/strategies/${strategyId}/start`)
  }

  async stopStrategy(strategyId: string): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string }>(`/v1/strategies/${strategyId}/stop`)
  }

  async pauseStrategy(strategyId: string): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string }>(`/v1/strategies/${strategyId}/pause`)
  }

  async resumeStrategy(strategyId: string): Promise<{ ok: boolean; message?: string }> {
    return this.post<{ ok: boolean; message?: string }>(`/v1/strategies/${strategyId}/resume`)
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
}

export const strategiesAPI = new StrategiesAPI()
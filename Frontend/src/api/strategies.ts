// Target path: Frontend/src/api/strategies.ts

import { APIClient } from './client'
import type {
  LoadStrategyPayload,
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

export class StrategiesAPI extends APIClient {
  async getRegistry(): Promise<RegisteredStrategy[]> {
    return this.get<RegisteredStrategy[]>('/v1/strategies/registry')
  }

  async getLoaded(): Promise<StrategyRuntimeInfo[]> {
    return this.get<StrategyRuntimeInfo[]>('/v1/strategies/loaded')
  }

  async getStatus(deploymentId: string): Promise<StrategyRuntimeInfo> {
    return this.get<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/status`)
  }

  async getParams(strategyId: string): Promise<StrategyParams> {
    return this.get<StrategyParams>(`/v1/strategies/${strategyId}/params`)
  }

  async updateParams(
    strategyId: string,
    params: StrategyParams,
  ): Promise<{ success: boolean; error?: string }> {
    return this.put<{ success: boolean; error?: string }>(
      `/v1/strategies/${strategyId}/params`,
      { config: params },
    )
  }

  async createStrategyCode(
    request: StrategyCodeCreateRequest,
  ): Promise<StrategyCodeVersion> {
    return this.post<StrategyCodeVersion>('/v1/strategies/code', request)
  }

  async getLatestStrategyCode(strategyId: string): Promise<StrategyCodeVersion> {
    return this.get<StrategyCodeVersion>(`/v1/strategies/${strategyId}/code/latest`)
  }

  async getStrategyCodeVersion(
    strategyId: string,
    codeVersion: number,
  ): Promise<StrategyCodeVersion> {
    return this.get<StrategyCodeVersion>(`/v1/strategies/${strategyId}/code/${codeVersion}`)
  }

  async debugStrategyCode(
    request: StrategyCodeDebugRequest,
  ): Promise<StrategyCodeDebugResponse> {
    return this.post<StrategyCodeDebugResponse>('/v1/strategies/code/debug', request)
  }

  async loadStrategy(
    strategyId: string,
    payload: LoadStrategyPayload,
  ): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/strategies/${strategyId}/load`, payload)
  }

  async unloadStrategy(deploymentId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/unload`)
  }

  async startStrategy(
    deploymentId: string,
    _legacySymbol?: string,
  ): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/start`)
  }

  async stopStrategy(deploymentId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/stop`)
  }

  async pauseStrategy(deploymentId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/pause`)
  }

  async resumeStrategy(deploymentId: string): Promise<StrategyRuntimeInfo> {
    return this.post<StrategyRuntimeInfo>(`/v1/deployments/${deploymentId}/resume`)
  }

  async getStrategyEvents(
    deploymentId: string,
    eventType?: string,
    limit = 100,
  ): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(
      `/v1/deployments/${deploymentId}/events`,
      { params: { event_type: eventType, limit } },
    )
  }

  async getStrategySignals(
    deploymentId: string,
    limit = 50,
  ): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(
      `/v1/deployments/${deploymentId}/events/signals`,
      { params: { limit } },
    )
  }

  async getStrategyErrors(
    deploymentId: string,
    limit = 50,
  ): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(
      `/v1/deployments/${deploymentId}/events/errors`,
      { params: { limit } },
    )
  }

  async getStrategyFills(
    deploymentId: string,
    limit = 100,
  ): Promise<StrategyEventEnvelope[]> {
    return this.get<StrategyEventEnvelope[]>(
      `/v1/deployments/${deploymentId}/events/fills`,
      { params: { limit } },
    )
  }

  async getTradingPairs(
    statusFilter = 'TRADING',
    quoteAsset = 'USDT',
  ): Promise<TradingPairsResponse> {
    return this.get<TradingPairsResponse>('/v1/exchange/trading-pairs', {
      params: { status_filter: statusFilter, quote_asset: quoteAsset },
    })
  }

  async getSafetyGateStatus(): Promise<SafetyGateStatus> {
    return this.get<SafetyGateStatus>('/v1/safety-gate/status')
  }

  async setSafetyGateEnabled(
    enabled: boolean,
    confirmed = false,
  ): Promise<SafetyGateStatus> {
    return this.post<SafetyGateStatus>('/v1/safety-gate/enable', {
      enabled,
      confirmed,
    })
  }
}

export const strategiesAPI = new StrategiesAPI()

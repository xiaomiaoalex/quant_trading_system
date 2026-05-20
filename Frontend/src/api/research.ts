import { APIClient } from './client'
import type {
  ActionResult,
  BinanceOHLCVIngestionRequest,
  BinanceOHLCVIngestionResult,
  BinanceOHLCVWorkerStatus,
  DataCatalogResponse,
  DataSourceStatus,
  OHLCVImportRequest,
  OHLCVImportResponse,
  PortfolioAutopilotDecision,
  PortfolioAutopilotSnapshot,
  PortfolioAutopilotTickRequest,
  PromotePaperResponse,
  StrategyAllocationProfile,
  StrategyAllocationProfileUpdateRequest,
  StrategyCandidate,
  StrategyCandidateCreateRequest,
  StrategyCandidateDebugRequest,
  StrategyCandidateDebugResponse,
  StrategyCandidateBacktestRequest,
} from '@/types'

export class ResearchAPI extends APIClient {
  async getDataCatalog(): Promise<DataCatalogResponse> {
    return this.get<DataCatalogResponse>('/v1/data/catalog')
  }

  async getOhlcvCoverage(featureVersion?: string): Promise<DataSourceStatus[]> {
    const query = featureVersion ? `?feature_version=${encodeURIComponent(featureVersion)}` : ''
    return this.get<DataSourceStatus[]>(`/v1/data/ohlcv/coverage${query}`)
  }

  async importOHLCV(request: OHLCVImportRequest): Promise<OHLCVImportResponse> {
    return this.post<OHLCVImportResponse>('/v1/data/ohlcv/import', request)
  }

  async syncBinanceOHLCV(
    request: BinanceOHLCVIngestionRequest,
  ): Promise<BinanceOHLCVIngestionResult> {
    return this.post<BinanceOHLCVIngestionResult>('/v1/data/ohlcv/sync-binance', request)
  }

  async startBinanceOHLCVWorker(
    request: BinanceOHLCVIngestionRequest,
  ): Promise<BinanceOHLCVWorkerStatus> {
    return this.post<BinanceOHLCVWorkerStatus>('/v1/data/ohlcv/worker/start', request)
  }

  async stopBinanceOHLCVWorker(): Promise<BinanceOHLCVWorkerStatus> {
    return this.post<BinanceOHLCVWorkerStatus>('/v1/data/ohlcv/worker/stop', {})
  }

  async getBinanceOHLCVWorkerStatus(): Promise<BinanceOHLCVWorkerStatus> {
    return this.get<BinanceOHLCVWorkerStatus>('/v1/data/ohlcv/worker/status')
  }

  async listCandidates(): Promise<StrategyCandidate[]> {
    return this.get<StrategyCandidate[]>('/v1/strategy-candidates')
  }

  async createCandidate(request: StrategyCandidateCreateRequest): Promise<StrategyCandidate> {
    return this.post<StrategyCandidate>('/v1/strategy-candidates', request)
  }

  async deleteCandidate(candidateId: string): Promise<ActionResult> {
    return this.delete<ActionResult>(`/v1/strategy-candidates/${candidateId}`)
  }

  async debugCandidate(
    candidateId: string,
    request: StrategyCandidateDebugRequest,
  ): Promise<StrategyCandidateDebugResponse> {
    return this.post<StrategyCandidateDebugResponse>(`/v1/strategy-candidates/${candidateId}/debug`, request)
  }

  async runCandidateBacktest(
    candidateId: string,
    request: StrategyCandidateBacktestRequest,
  ): Promise<StrategyCandidate> {
    return this.post<StrategyCandidate>(`/v1/strategy-candidates/${candidateId}/backtests`, request)
  }

  async validateCandidate(candidateId: string): Promise<StrategyCandidate> {
    return this.post<StrategyCandidate>(`/v1/strategy-candidates/${candidateId}/validate`)
  }

  async promoteCandidateToPaper(candidateId: string): Promise<PromotePaperResponse> {
    return this.post<PromotePaperResponse>(`/v1/strategy-candidates/${candidateId}/promote-paper`)
  }

  async listAllocations(): Promise<StrategyAllocationProfile[]> {
    return this.get<StrategyAllocationProfile[]>('/v1/allocations')
  }

  async upsertAllocation(
    deploymentId: string,
    request: StrategyAllocationProfileUpdateRequest,
  ): Promise<StrategyAllocationProfile> {
    return this.put<StrategyAllocationProfile>(`/v1/allocations/${deploymentId}`, request)
  }

  async getAutopilotSnapshot(): Promise<PortfolioAutopilotSnapshot> {
    return this.get<PortfolioAutopilotSnapshot>('/v1/portfolio-autopilot/snapshot')
  }

  async tickAutopilot(request: PortfolioAutopilotTickRequest): Promise<PortfolioAutopilotSnapshot> {
    return this.post<PortfolioAutopilotSnapshot>('/v1/portfolio-autopilot/tick', request)
  }

  async listAutopilotDecisions(): Promise<PortfolioAutopilotDecision[]> {
    return this.get<PortfolioAutopilotDecision[]>('/v1/portfolio-autopilot/decisions')
  }
}

export const researchAPI = new ResearchAPI()

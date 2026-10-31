import type { z } from 'zod'
import { APIClient } from './client'
import {
  ResearchBacktestEventResultSchema,
  ResearchBacktestRunEnvelopeSchema,
  ResearchBacktestRunListEnvelopeSchema,
  ResearchDatasetListSchema,
  ResearchStrategyListSchema,
} from '@/contracts/research'
import type {
  ActionResult,
  BinanceOHLCVIngestionRequest,
  BinanceOHLCVIngestionResult,
  BinanceOHLCVWorkerStatus,
  DataCatalogResponse,
  DataSourceStatus,
  HistoryDataset,
  HistoryJob,
  HistoryJobCreateRequest,
  OHLCVImportRequest,
  OHLCVImportResponse,
  PortfolioAutopilotDecision,
  PortfolioAutopilotSnapshot,
  PortfolioAutopilotTickRequest,
  PromotePaperResponse,
  ResearchBacktestCreateRequest,
  ResearchBacktestRun,
  ResearchDatasetQuery,
  ResearchDatasetSummary,
  ResearchStrategySummary,
  StrategyAllocationProfile,
  StrategyAllocationProfileUpdateRequest,
  StrategyCandidate,
  StrategyCandidateCreateRequest,
  StrategyCandidateDebugRequest,
  StrategyCandidateDebugResponse,
  StrategyCandidateBacktestRequest,
} from '@/types'

/** 运行时契约校验：响应漂移不能被静默渲染。 */
function parseOrThrow<T>(schema: z.ZodType<T>, payload: unknown, label: string): T {
  const parsed = schema.safeParse(payload)
  if (!parsed.success) {
    const detail = parsed.error.issues
      .map(issue => `${issue.path.join('.') || '<root>'}: ${issue.message}`)
      .join('; ')
    throw new Error(`${label} schema mismatch: ${detail}`)
  }
  return parsed.data
}

/**
 * 运行记录解析：外层信封统一校验；事件运行的结果再按严格契约校验。
 * legacy 结果结构与事件运行不同，不作为准入证据，直接丢弃。
 */
function parseResearchBacktestRun(raw: unknown, label: string): ResearchBacktestRun {
  const envelope = parseOrThrow(ResearchBacktestRunEnvelopeSchema, raw, label)
  let result: ResearchBacktestRun['result'] = null
  if (
    envelope.model === 'event_runtime' &&
    envelope.result !== null &&
    envelope.result !== undefined
  ) {
    result = parseOrThrow(
      ResearchBacktestEventResultSchema,
      envelope.result,
      `${label} result`,
    )
  }
  return { ...envelope, result }
}

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

  // ==================== History data (Binance Vision archives) ====================

  async createHistoryJob(request: HistoryJobCreateRequest): Promise<HistoryJob> {
    return this.post<HistoryJob>('/v1/data/history/jobs', request)
  }

  async listHistoryJobs(params?: {
    status?: string
    symbol?: string
    month?: string
    limit?: number
  }): Promise<HistoryJob[]> {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.symbol) searchParams.set('symbol', params.symbol)
    if (params?.month) searchParams.set('month', params.month)
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const query = searchParams.toString()
    return this.get<HistoryJob[]>(`/v1/data/history/jobs${query ? `?${query}` : ''}`)
  }

  async getHistoryJob(jobId: string): Promise<HistoryJob> {
    return this.get<HistoryJob>(`/v1/data/history/jobs/${jobId}`)
  }

  async retryHistoryJob(jobId: string): Promise<HistoryJob> {
    return this.post<HistoryJob>(`/v1/data/history/jobs/${jobId}/retry`, {})
  }

  async listHistoryDatasets(params?: {
    symbol?: string
    month?: string
    only_eligible?: boolean
    limit?: number
  }): Promise<HistoryDataset[]> {
    const searchParams = new URLSearchParams()
    if (params?.symbol) searchParams.set('symbol', params.symbol)
    if (params?.month) searchParams.set('month', params.month)
    if (params?.only_eligible !== undefined) {
      searchParams.set('only_eligible', String(params.only_eligible))
    }
    if (params?.limit) searchParams.set('limit', String(params.limit))
    const query = searchParams.toString()
    return this.get<HistoryDataset[]>(`/v1/data/history/datasets${query ? `?${query}` : ''}`)
  }

  async getHistoryDataset(datasetId: string): Promise<HistoryDataset> {
    return this.get<HistoryDataset>(`/v1/data/history/datasets/${datasetId}`)
  }

  // ==================== Research plane (datasets / strategies / event backtests) ====================

  /** Research 数据集目录（只读元数据）。 */
  async listResearchDatasets(params?: ResearchDatasetQuery): Promise<ResearchDatasetSummary[]> {
    const searchParams = new URLSearchParams()
    if (params?.symbol) searchParams.set('symbol', params.symbol)
    if (params?.month) searchParams.set('month', params.month)
    if (params?.official_only !== undefined) {
      searchParams.set('official_only', String(params.official_only))
    }
    if (params?.limit !== undefined) searchParams.set('limit', String(params.limit))
    if (params?.offset !== undefined) searchParams.set('offset', String(params.offset))
    const query = searchParams.toString()
    const raw = await this.get<unknown>(`/v1/research/datasets${query ? `?${query}` : ''}`)
    return parseOrThrow(ResearchDatasetListSchema, raw, 'Research datasets')
  }

  /** 已安装、允许提交回测的策略。 */
  async listResearchStrategies(): Promise<ResearchStrategySummary[]> {
    const raw = await this.get<unknown>('/v1/research/strategies')
    return parseOrThrow(ResearchStrategyListSchema, raw, 'Research strategies')
  }

  /** 提交事件回测（engine=event_runtime, purpose=research_screening）。 */
  async createResearchBacktest(
    request: ResearchBacktestCreateRequest,
  ): Promise<ResearchBacktestRun> {
    const raw = await this.post<unknown>('/v1/research/backtests', request)
    return parseResearchBacktestRun(raw, 'Research backtest run')
  }

  async listResearchBacktests(params?: { limit?: number }): Promise<ResearchBacktestRun[]> {
    const limit = params?.limit ?? 100
    const raw = await this.get<unknown>(`/v1/research/backtests?limit=${encodeURIComponent(limit)}`)
    const envelopes = parseOrThrow(
      ResearchBacktestRunListEnvelopeSchema,
      raw,
      'Research backtest runs',
    )
    return envelopes.map(envelope => parseResearchBacktestRun(envelope, 'Research backtest run'))
  }

  async getResearchBacktest(runId: string): Promise<ResearchBacktestRun> {
    const raw = await this.get<unknown>(
      `/v1/research/backtests/${encodeURIComponent(runId)}`,
    )
    return parseResearchBacktestRun(raw, 'Research backtest run')
  }

  /** 重试 FAILED 运行；非 FAILED 返回 409，不存在返回 404。 */
  async retryResearchBacktest(runId: string): Promise<ResearchBacktestRun> {
    const raw = await this.post<unknown>(
      `/v1/research/backtests/${encodeURIComponent(runId)}/retry`,
      {},
    )
    return parseResearchBacktestRun(raw, 'Research backtest retry')
  }

  async cancelResearchBacktest(runId: string): Promise<ResearchBacktestRun> {
    const raw = await this.post<unknown>(
      `/v1/research/backtests/${encodeURIComponent(runId)}/cancel`,
      {},
    )
    return parseResearchBacktestRun(raw, 'Research backtest cancel')
  }

  async listCandidates(): Promise<StrategyCandidate[]> {
    return this.get<StrategyCandidate[]>('/v1/strategy-candidates')
  }

  async getCandidate(candidateId: string): Promise<StrategyCandidate> {
    return this.get<StrategyCandidate>(`/v1/strategy-candidates/${candidateId}`)
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

import { APIClient } from './client'
import type {
  ActionResult,
  DataCatalogResponse,
  PortfolioAutopilotDecision,
  PortfolioAutopilotSnapshot,
  PortfolioAutopilotTickRequest,
  StrategyAllocationProfile,
  StrategyAllocationProfileUpdateRequest,
  StrategyCandidate,
  StrategyCandidateCreateRequest,
  StrategyCandidatePromoteRequest,
} from '@/types'

export class ResearchAPI extends APIClient {
  async getDataCatalog(): Promise<DataCatalogResponse> {
    return this.get<DataCatalogResponse>('/v1/data/catalog')
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

  async promoteCandidate(
    candidateId: string,
    request: StrategyCandidatePromoteRequest,
  ): Promise<StrategyCandidate> {
    return this.post<StrategyCandidate>(`/v1/strategy-candidates/${candidateId}/promote`, request)
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

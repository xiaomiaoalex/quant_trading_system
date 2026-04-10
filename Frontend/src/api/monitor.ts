import { APIClient } from './client';
import type {
  MonitorSnapshot,
  MonitorAlertsResponse,
  AlertRule,
  ActionResult,
  KillSwitchState,
  HealthResponse,
  HealthDependency,
} from '@/types';

// TODO: BLOCKED BY BACKEND API
// The following capabilities are waiting for backend implementation:
// - GET /v1/monitor/snapshot should return truly aggregated data without query parameters (Task 9.2)
// - POST /v1/reconciler/trigger should support no-parameter trigger (Task 9.3)

export class MonitorAPI extends APIClient {
  async getSnapshot(): Promise<MonitorSnapshot> {
    return this.get<MonitorSnapshot>('/v1/monitor/snapshot');
  }

  async getAlerts(): Promise<MonitorAlertsResponse> {
    return this.get<MonitorAlertsResponse>('/v1/monitor/alerts');
  }

  async createAlertRule(rule: AlertRule): Promise<ActionResult> {
    return this.post<ActionResult>('/v1/monitor/rules', rule);
  }

  async deleteAlertRule(ruleName: string): Promise<ActionResult> {
    return this.delete<ActionResult>(`/v1/monitor/rules/${encodeURIComponent(ruleName)}`);
  }

  async clearAlert(ruleName: string, reason?: string): Promise<ActionResult> {
    return this.post<ActionResult>(`/v1/monitor/alerts/${encodeURIComponent(ruleName)}/clear`, {
      reason,
    });
  }

  async clearAllAlerts(reason?: string): Promise<ActionResult> {
    return this.post<ActionResult>('/v1/monitor/alerts/clear-all', { reason });
  }

  async getKillSwitch(): Promise<KillSwitchState> {
    return this.get<KillSwitchState>('/v1/killswitch');
  }

  async getReadiness(): Promise<HealthResponse> {
    return this.get<HealthResponse>('/health/ready');
  }

  async getDependencyHealth(): Promise<HealthDependency[]> {
    return this.get<HealthDependency[]>('/health/dependency');
  }
}

export const monitorAPI = new MonitorAPI();

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMonitorSnapshot } from '@/hooks';
import { monitorAPI } from '@/api';
import type { MonitorSnapshot } from '@/types';

// Mock the API
vi.mock('@/api', () => ({
  monitorAPI: {
    getSnapshot: vi.fn(),
    getAlerts: vi.fn(),
    clearAlert: vi.fn(),
    clearAllAlerts: vi.fn(),
  },
}));

// Mock types
const createMockSnapshot = (overrides = {}): MonitorSnapshot => ({
  timestamp: new Date().toISOString(),
  total_positions: 5,
  total_exposure: '100000',
  open_orders_count: 3,
  pending_orders_count: 2,
  daily_pnl: '1500.50',
  daily_pnl_pct: '1.5',
  realized_pnl: '1200.00',
  unrealized_pnl: '300.50',
  killswitch_level: 0,
  killswitch_scope: 'GLOBAL',
  adapters: {
    'binance-spot': {
      adapter_name: 'binance-spot',
      status: 'HEALTHY',
      last_heartbeat_ts_ms: Date.now(),
      error_count: 0,
    },
  },
  active_alerts: [],
  alert_count_by_severity: { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 },
  ...overrides,
});

// Wrapper for test
const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('useMonitorSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should return loading state initially', async () => {
    vi.mocked(monitorAPI.getSnapshot).mockImplementation(
      () => new Promise(() => {}), // Never resolves
    );

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.snapshot).toBeNull();
    expect(result.current.isStale).toBe(true);
  });

  it('should return snapshot data on success', async () => {
    const mockSnapshot = createMockSnapshot();
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(mockSnapshot);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.snapshot).toEqual(mockSnapshot);
    expect(result.current.isError).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('should return error state on failure', async () => {
    const error = new Error('Test error message');
    (error as unknown as { code: string }).code = 'TEST_ERROR';
    vi.mocked(monitorAPI.getSnapshot).mockRejectedValue(error);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    }, { timeout: 5000 });

    expect(result.current.isError).toBe(true);
    expect(result.current.error).toBe('TEST_ERROR: Test error message');
    expect(result.current.snapshot).toBeNull();
  });

  it('should detect stale snapshot', async () => {
    const staleSnapshot = createMockSnapshot({
      timestamp: new Date(Date.now() - 120_000).toISOString(), // 2 minutes ago
    });
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(staleSnapshot);

    const { result } = renderHook(() => useMonitorSnapshot({ staleThresholdMs: 60_000 }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isStale).toBe(true);
  });

  it('should derive healthy state from snapshot', async () => {
    const healthySnapshot = createMockSnapshot({
      killswitch_level: 0,
      adapters: {
        'binance-spot': {
          adapter_name: 'binance-spot',
          status: 'HEALTHY',
          error_count: 0,
        },
      },
    });
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(healthySnapshot);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.healthState).toBe('healthy');
  });

  it('should derive degraded state with degraded adapter', async () => {
    const degradedSnapshot = createMockSnapshot({
      adapters: {
        'binance-spot': {
          adapter_name: 'binance-spot',
          status: 'DEGRADED',
          error_count: 5,
        },
      },
    });
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(degradedSnapshot);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.healthState).toBe('degraded');
  });

  it('should derive down state with down adapter', async () => {
    const downSnapshot = createMockSnapshot({
      adapters: {
        'binance-spot': {
          adapter_name: 'binance-spot',
          status: 'DOWN',
          error_count: 100,
        },
      },
    });
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(downSnapshot);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.healthState).toBe('down');
  });

  it('should derive down state with L2 killswitch', async () => {
    const stoppedSnapshot = createMockSnapshot({
      killswitch_level: 2, // CLOSE_ONLY
      adapters: {
        'binance-spot': {
          adapter_name: 'binance-spot',
          status: 'HEALTHY',
          error_count: 0,
        },
      },
    });
    vi.mocked(monitorAPI.getSnapshot).mockResolvedValue(stoppedSnapshot);

    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.healthState).toBe('down');
  });

  it('should return stale state when snapshot is null', () => {
    const { result } = renderHook(() => useMonitorSnapshot(), {
      wrapper: createWrapper(),
    });

    expect(result.current.healthState).toBe('down');
    expect(result.current.isStale).toBe(true);
  });
});

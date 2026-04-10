import { describe, it, expect } from 'vitest';
import { isStale, timeSince, formatTimestamp, formatTsMs } from '@/utils/stale';

describe('isStale', () => {
  it('should return true for null timestamp', () => {
    expect(isStale(null)).toBe(true);
  });

  it('should return true for undefined timestamp', () => {
    expect(isStale(undefined)).toBe(true);
  });

  it('should return true for timestamp older than threshold', () => {
    const oldTimestamp = new Date(Date.now() - 120_000).toISOString(); // 2 minutes ago
    expect(isStale(oldTimestamp, 60_000)).toBe(true);
  });

  it('should return false for recent timestamp', () => {
    const recentTimestamp = new Date(Date.now() - 30_000).toISOString(); // 30 seconds ago
    expect(isStale(recentTimestamp, 60_000)).toBe(false);
  });

  it('should accept Date object', () => {
    const recentDate = new Date(Date.now() - 30_000);
    expect(isStale(recentDate, 60_000)).toBe(false);
  });
});

describe('timeSince', () => {
  it('should return "unknown" for null', () => {
    expect(timeSince(null)).toBe('unknown');
  });

  it('should return "just now" for very recent', () => {
    const recent = new Date(Date.now() - 2_000).toISOString();
    expect(timeSince(recent)).toBe('just now');
  });

  it('should return seconds for < 60s', () => {
    const recent = new Date(Date.now() - 30_000).toISOString();
    expect(timeSince(recent)).toBe('30s ago');
  });

  it('should return minutes for < 1 hour', () => {
    const recent = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(timeSince(recent)).toBe('5m ago');
  });

  it('should return hours for < 1 day', () => {
    const recent = new Date(Date.now() - 3 * 3600_000).toISOString();
    expect(timeSince(recent)).toBe('3h ago');
  });

  it('should return days for >= 1 day', () => {
    const recent = new Date(Date.now() - 2 * 86400_000).toISOString();
    expect(timeSince(recent)).toBe('2d ago');
  });
});

describe('formatTimestamp', () => {
  it('should return "—" for null', () => {
    expect(formatTimestamp(null)).toBe('—');
  });

  it('should format valid timestamp', () => {
    const timestamp = '2024-01-15T10:30:00';
    const result = formatTimestamp(timestamp);
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/);
  });
});

describe('formatTsMs', () => {
  it('should return "—" for null', () => {
    expect(formatTsMs(null)).toBe('—');
  });

  it('should return "—" for undefined', () => {
    expect(formatTsMs(undefined)).toBe('—');
  });

  it('should format valid timestamp', () => {
    const ts = new Date('2024-01-15T10:30:00').getTime();
    const result = formatTsMs(ts);
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/);
  });
});

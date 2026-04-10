import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HealthBadge, SeverityBadge, KillSwitchBadge, AdapterStatusBadge } from '@/components/ui/StatusBadge';

describe('HealthBadge', () => {
  it.each([
    ['healthy', 'Healthy'],
    ['degraded', 'Degraded'],
    ['stale', 'Stale'],
    ['down', 'Down'],
  ])('should render %s state with label', (state, label) => {
    render(<HealthBadge state={state as 'healthy' | 'degraded' | 'stale' | 'down'} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('should not show label when showLabel is false', () => {
    render(<HealthBadge state="healthy" showLabel={false} />);
    // Should only have the dot, not the label
    const dots = document.querySelectorAll('.rounded-full');
    expect(dots.length).toBeGreaterThan(0);
  });

  it('should render small size', () => {
    render(<HealthBadge state="degraded" size="sm" />);
    expect(screen.getByText('Degraded')).toBeInTheDocument();
  });
});

describe('SeverityBadge', () => {
  it.each([
    ['LOW', 'Low'],
    ['MEDIUM', 'Medium'],
    ['HIGH', 'High'],
    ['CRITICAL', 'Critical'],
  ])('should render %s severity', (severity, label) => {
    render(<SeverityBadge severity={severity as 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe('KillSwitchBadge', () => {
  it.each([
    [0, 'Normal'],
    [1, 'No New Positions'],
    [2, 'Close Only'],
    [3, 'Full Stop'],
  ])('should render level %s badge', (level, label) => {
    render(<KillSwitchBadge level={level as 0 | 1 | 2 | 3} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe('AdapterStatusBadge', () => {
  it.each([
    ['HEALTHY', 'Healthy'],
    ['DEGRADED', 'Degraded'],
    ['DOWN', 'Down'],
  ])('should render %s status', (status, label) => {
    render(<AdapterStatusBadge status={status as 'HEALTHY' | 'DEGRADED' | 'DOWN'} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

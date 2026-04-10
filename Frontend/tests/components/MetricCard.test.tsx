import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MetricCard } from '@/components/monitor/MetricCard';

describe('MetricCard', () => {
  it('should render title and value', () => {
    render(<MetricCard title="Open Orders" value={42} />);
    expect(screen.getByText('Open Orders')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('should render subValue when provided', () => {
    render(<MetricCard title="Daily P&L" value="1500.50" subValue="1.5%" />);
    expect(screen.getByText('1.5%')).toBeInTheDocument();
  });

  it('should show loading skeleton when isLoading', () => {
    render(<MetricCard title="Open Orders" value={0} isLoading />);
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('should render trend indicator when provided', () => {
    render(<MetricCard title="P&L" value="100" trend="up" trendValue="5%" />);
    expect(screen.getByText('↑ 5%')).toBeInTheDocument();
  });

  it('should render down trend correctly', () => {
    render(<MetricCard title="P&L" value="-50" trend="down" trendValue="3%" />);
    expect(screen.getByText('↓ 3%')).toBeInTheDocument();
  });

  it('should apply custom className', () => {
    const { container } = render(
      <MetricCard title="Test" value="test" className="custom-class" />,
    );
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('should handle numeric and string values', () => {
    const { rerender } = render(<MetricCard title="Count" value={123} />);
    expect(screen.getByText('123')).toBeInTheDocument();

    rerender(<MetricCard title="Amount" value="123.45" />);
    expect(screen.getByText('123.45')).toBeInTheDocument();
  });
});

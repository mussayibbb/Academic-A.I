// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EarlyWarningRiskTrajectory from '@/components/EarlyWarningRiskTrajectory';
import EarlyWarningsPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));
vi.mock('next/font/google', () => ({
  Bricolage_Grotesque: () => ({ variable: 'font-heading' }),
  DM_Sans: () => ({ variable: 'font-body' }),
}));

describe('teacher early warnings', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation(() => ({ matches: true, addListener: vi.fn(), removeListener: vi.fn() })),
    });
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.startsWith('/api/v1/early-warnings?')) return {
        summary: { signals: 2, highPriority: 1, watchList: 1, recoveredThisMonth: 0, modelRefreshedAt: '2026-11-22T12:00:00Z' },
        items: [{
          studentId: 17,
          name: 'Test Student',
          department: 'Sciences',
          riskProbability: 0.82,
          riskClass: 'At risk',
          priority: 'High',
          topFactor: 'Attendance',
        }],
        nextPage: null,
      };
      if (path === '/api/v1/early-warnings/17') return {
        studentId: 17,
        name: 'Test Student',
        department: 'Sciences',
        riskProbability: 0.82,
        riskClass: 'At risk',
        priority: 'High',
        confidence: 0.9,
        signalsUsed: 4,
        weekChange: 5,
        insufficientData: false,
        trend: [{ weekStart: '2026-11-16', risk: 0.82 }],
        forecast: { noAction: 0.9, withPlan: 0.35 },
        factors: [
          { key: 'attendance', label: 'Attendance', detail: '71% now', currentValue: 71, previousValue: 84, unit: '%', contributionShare: 0.6 },
          { key: 'activity_per_week', label: 'Portal activity', detail: '2 logins per week', currentValue: 2, previousValue: 1, unit: 'logins/week', contributionShare: 0.4 },
        ],
        precautions: [{ factorKey: 'attendance', text: 'Agree on an attendance target of 85%.' }],
        actions: [],
      };
      if (path === '/api/v1/early-warnings/17/simulate' && init?.method === 'POST') {
        const recoveries = JSON.parse(String(init.body)).recoveries;
        const riskProbability = recoveries.attendance === 0.01
          ? 0.819
          : recoveries.activity_per_week >= 0.5
            ? 0.25
            : recoveries.attendance >= 1
              ? 0.32
              : 0.68;
        const riskClass = riskProbability >= 0.75 ? 'At risk' : riskProbability >= 0.4 ? 'Watch' : 'Normal';
        return { riskProbability, riskClass, delta: Number(((0.82 - riskProbability) * 100).toFixed(2)) };
      }
      if (path === '/api/v1/interventions/meetings' && init?.method === 'POST') {
        return { type: 'meeting', label: 'Host a meeting', at: '2026-11-22T12:00:00Z', by: 3 };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads the list and selected detail, then saves a meeting', async () => {
    render(<EarlyWarningsPage />);
    expect(await screen.findByText('Test Student')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Why the model flagged this student' })).toBeTruthy();
    expect(await screen.findByText('Predicted risk')).toBeTruthy();
    expect(screen.getByText('Predicted risk').parentElement?.textContent).toContain('82%');
    const slider = screen.getByLabelText('Attendance recovers') as HTMLInputElement;
    expect(slider.value).toBe('0');
    const predictedBox = screen.getByText('Predicted risk').parentElement as HTMLElement;
    expect(within(predictedBox).getByText('Danger')).toBeTruthy();
    expect((within(predictedBox).getByText('82%') as HTMLElement).style.color).toBe('var(--hi)');
    expect(screen.getByText('Currently 82%. This scenario changes it by 0 pts.')).toBeTruthy();
    fireEvent.change(slider, { target: { value: '50' } });
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/early-warnings/17/simulate',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ recoveries: { attendance: 0.5, activity_per_week: 0 } }) }),
    ));
    expect(await screen.findByText('68%')).toBeTruthy();
    expect(within(slider.closest('.ew-card') as HTMLElement).getByText('Watch')).toBeTruthy();
    expect((within(predictedBox).getByText('68%') as HTMLElement).style.color).toBe('var(--md)');
    expect(screen.getByText('Currently 82%. This scenario changes it by +14 pts.')).toBeTruthy();
    fireEvent.change(slider, { target: { value: '100' } });
    expect(await screen.findByText('32%')).toBeTruthy();
    expect(within(predictedBox).getByText('Normal')).toBeTruthy();
    expect((within(predictedBox).getByText('32%') as HTMLElement).style.color).toBe('var(--lo)');
    const portalSlider = screen.getByLabelText('Portal activity recovers') as HTMLInputElement;
    fireEvent.change(portalSlider, { target: { value: '50' } });
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/early-warnings/17/simulate',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ recoveries: { attendance: 1, activity_per_week: 0.5 } }) }),
    ));
    expect(await screen.findByText('25%')).toBeTruthy();
    expect(screen.getByText('Currently 82%. This scenario changes it by +57 pts.')).toBeTruthy();
    fireEvent.change(slider, { target: { value: '1' } });
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/early-warnings/17/simulate',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ recoveries: { attendance: 0.01, activity_per_week: 0.5 } }) }),
    ));
    expect(await screen.findByText('81.9%')).toBeTruthy();
    expect(screen.getByText('Currently 82%. This scenario changes it by +0.1 pts.')).toBeTruthy();
    const meeting = await screen.findByRole('button', { name: /Host a meeting/i });
    meeting.click();
    await waitFor(() => expect(screen.getByText('Schedule a meeting')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Tomorrow 10:00 am' }));
    expect((screen.getByLabelText('Meeting date and time') as HTMLInputElement).value).toMatch(/T10:00$/);
    fireEvent.change(screen.getByLabelText('Meeting note'), { target: { value: 'Review attendance supports' } });
    screen.getByRole('button', { name: 'Confirm' }).click();
    expect(await screen.findByText(/Host a meeting ·/)).toBeTruthy();
    expect(await screen.findByText('Host a meeting saved')).toBeTruthy();
    expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/interventions/meetings',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"note":"Review attendance supports"'),
      }),
    );
  }, 15000);

  it('keeps detail and the last prediction when simulation fails', async () => {
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.startsWith('/api/v1/early-warnings?')) return {
        summary: { signals: 1, highPriority: 1, watchList: 0, recoveredThisMonth: 0, modelRefreshedAt: '2026-11-22T12:00:00Z' },
        items: [{ studentId: 17, name: 'Test Student', department: 'Sciences', riskProbability: 0.82, riskClass: 'At risk', priority: 'High', topFactor: 'Attendance' }],
        nextPage: null,
      };
      if (path === '/api/v1/early-warnings/17') return {
        studentId: 17, name: 'Test Student', department: 'Sciences', riskProbability: 0.82, riskClass: 'At risk', priority: 'High',
        confidence: 0.9, signalsUsed: 4, weekChange: 5, insufficientData: false, trend: [], forecast: { noAction: 0.9, withPlan: 0.35 },
        factors: [{ key: 'attendance', label: 'Attendance', detail: '71% now', currentValue: 71, previousValue: 84, unit: '%', contributionShare: 1 }],
        precautions: [], actions: [],
      };
      if (path.endsWith('/simulate') && init?.method === 'POST') throw new Error('Temporary model failure');
      throw new Error(`Unexpected API call: ${path}`);
    });
    render(<EarlyWarningsPage />);
    expect(await screen.findByText('Predicted risk')).toBeTruthy();
    const slider = screen.getByLabelText('Attendance recovers') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: '50' } });
    expect(await screen.findByText(/Temporary model failure/)).toBeTruthy();
    expect(screen.getByText('Predicted risk').parentElement?.textContent).toContain('82%');
    expect(screen.queryByText('Could not load student detail')).toBeNull();
  }, 15000);

  it('shows an honest empty state for an unvalidated demo model', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/v1/early-warnings?')) return {
        summary: { signals: 0, highPriority: 0, watchList: 0, recoveredThisMonth: 0, modelRefreshedAt: '2026-11-22T12:00:00Z' },
        items: [],
        nextPage: null,
        dataStatus: 'unvalidated-demo-model',
        message: 'Validated Early Warnings are unavailable because the active risk model was trained on DEMO DATA.',
      };
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<EarlyWarningsPage />);

    expect(await screen.findByText('DEMO / RULE-GENERATED PREVIEW')).toBeTruthy();
    expect(screen.getByText(/trained on DEMO DATA/)).toBeTruthy();
    expect(screen.getByText('No validated student risk results are available.')).toBeTruthy();
    expect(mocks.api).not.toHaveBeenCalledWith('/api/v1/early-warnings/17');
  });

  it('draws an eight-point history and both forecast paths', () => {
    const trend = Array.from({ length: 8 }, (_, index) => ({
      weekStart: `2026-0${index + 1}-01`,
      risk: 0.3 + index * 0.05,
    }));
    const { container } = render(
      <EarlyWarningRiskTrajectory trend={trend} forecast={{ noAction: 0.8, withPlan: 0.4 }} currentRisk={0.65} animateIn />,
    );
    expect(container.querySelector('.ew-trend-line')?.getAttribute('pathLength')).toBe('1');
    expect(container.querySelector('.ew-area')).toBeTruthy();
    expect(container.querySelectorAll('.ew-forecast')).toHaveLength(2);
    expect(container.querySelector('.ew-now-dot')).toBeTruthy();
  });

  it('keeps the chart and forecast paths visible with no weekly history', () => {
    const { container } = render(
      <EarlyWarningRiskTrajectory trend={[]} forecast={{ noAction: 0.8, withPlan: 0.4 }} currentRisk={0.65} animateIn />,
    );
    expect(container.querySelector('svg[viewBox="0 0 400 136"]')).toBeTruthy();
    expect(container.querySelector('.ew-trend-line')).toBeNull();
    expect(container.querySelectorAll('.ew-forecast')).toHaveLength(2);
    expect(container.querySelector('.ew-now-dot')).toBeTruthy();
    expect(screen.getByText('Weekly history is still building. The forecast is based on the latest data.')).toBeTruthy();
  });
});

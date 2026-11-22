// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import EarlyWarningsPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

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
        factors: [{ key: 'attendance', label: 'Attendance', detail: '71% now', currentValue: 71, previousValue: 84, unit: '%', contributionShare: 1 }],
        precautions: [{ factorKey: 'attendance', text: 'Agree on an attendance target of 85%.' }],
        actions: [],
      };
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
    const meeting = await screen.findByRole('button', { name: /Host a meeting/i });
    meeting.click();
    await waitFor(() => expect(screen.getByText('Schedule a meeting')).toBeTruthy());
    screen.getByRole('button', { name: 'Confirm' }).click();
    expect(await screen.findByText(/Host a meeting ·/)).toBeTruthy();
  }, 15000);
});

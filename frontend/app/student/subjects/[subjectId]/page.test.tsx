// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentSubjectWorkspacePage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api, readCachedApiData: () => null }));
vi.mock('next/navigation', () => ({ useParams: () => ({ subjectId: '12' }) }));
vi.mock('@/components/StudentLearningRecoveryPanel', () => ({
  default: ({ subjectId }: { subjectId: number }) => <div>Learning assignments for subject {subjectId}</div>,
}));

describe('student subject recovery workspace', () => {
  beforeEach(() => {
    mocks.api.mockResolvedValue({
      policy: { version: 1, threshold_score: 12, threshold_maximum: 25, strictly_below: true, active_for_classification: true },
      subjects: [{
        id: 12,
        code: 'BIO-101',
        name: 'Biology',
        status: 'LOW',
        reason: 'Latest recorded result is 22 / 50 (11.00 / 25 normalized), strictly below the configured 12 / 25 support threshold.',
        latest_assessment: {
          evidence_id: 'mark:3',
          assessment: 'FA-1',
          score: 22,
          maximum_marks: 50,
          maximum_marks_verified: true,
          normalized_score: 11,
          recorded_at: '2026-10-01T00:00:00',
          official_result: null,
          source: 'Recorded assessment',
          academic_period: null,
          evidence_status: 'VERIFIED',
          reason: null,
        },
        latest_assessments: [],
        official_failure: null,
        support_triggers: ['LOW_SCORE'],
        history: [],
      }],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows the actual normalized evidence and keeps official results separate', async () => {
    render(<StudentSubjectWorkspacePage />);
    expect(await screen.findByRole('heading', { name: 'Biology' })).toBeTruthy();
    expect(screen.getByText('22 / 50')).toBeTruthy();
    expect(screen.getByText('11.00 / 25')).toBeTruthy();
    expect(screen.getByText('not recorded')).toBeTruthy();
    expect(screen.getByText('Learning assignments for subject 12')).toBeTruthy();
  });
});

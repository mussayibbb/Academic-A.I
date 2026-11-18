import { describe, expect, it } from 'vitest';
import { strictRecoveryEligibleSubjects } from './recovery-eligibility';

function focus(scores: {
  id: number;
  score: number | null;
  status?: string;
  maximumVerified?: boolean;
  recordedAt?: string | null;
}[]) {
  return {
    evidence_scope: 'FIRST_INTERNAL',
    policy: {
      threshold_score: 12,
      threshold_maximum: 25,
      strictly_below: true,
      active_for_classification: true,
    },
    subjects: scores.map(({ id, score, status = 'VERIFIED', maximumVerified = true, recordedAt = '2026-10-01T09:00:00Z' }) => ({
      id,
      students: [{
        id: 1,
        latest_assessment: score === null ? null : {
          evidence_status: status,
          maximum_marks_verified: maximumVerified,
          normalized_score: score,
          recorded_at: recordedAt,
        },
      }],
    })),
  };
}

describe('strict recovery eligibility', () => {
  it('includes verified, dated FA-1 evidence strictly below 12/25', () => {
    expect(strictRecoveryEligibleSubjects(focus([{ id: 4, score: 11.99 }]), 1).map(({ id }) => id)).toEqual([4]);
  });

  it('excludes evidence exactly at 12/25', () => {
    expect(strictRecoveryEligibleSubjects(focus([{ id: 4, score: 12 }]), 1)).toEqual([]);
  });

  it('excludes evidence without a date or verified maximum', () => {
    const response = focus([
      { id: 4, score: 11, recordedAt: null },
      { id: 5, score: 10, maximumVerified: false },
      { id: 6, score: 9, status: 'INSUFFICIENT_EVIDENCE' },
      { id: 7, score: null },
    ]);

    expect(strictRecoveryEligibleSubjects(response, 1)).toEqual([]);
  });

  it('returns every strictly eligible subject without a fixed subject limit', () => {
    const response = focus(Array.from({ length: 7 }, (_, index) => ({ id: index + 1, score: 11 - index / 10 })));

    expect(strictRecoveryEligibleSubjects(response, 1).map(({ id }) => id)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });
});

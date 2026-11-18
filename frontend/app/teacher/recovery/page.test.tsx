// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeacherRecoveryPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

describe('teacher subject-first recovery landing', () => {
  beforeEach(() => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/v1/teacher/subject-focus')) return {
        evidence_scope: 'FIRST_INTERNAL',
        policy: { version: 1, threshold_score: 12, threshold_maximum: 25, strictly_below: true, active_for_classification: true },
        subjects: [{
          id: 12,
          code: 'BIO-101',
          name: 'Biology',
          students: [
            { id: 4, status: 'LOW', latest_assessment: { evidence_status: 'VERIFIED', maximum_marks_verified: true, normalized_score: 11, recorded_at: '2026-10-01T09:00:00Z' } },
            { id: 5, status: 'INSUFFICIENT_EVIDENCE', latest_assessment: null },
          ],
          counts: { LOW: 1, NOT_LOW: 0, INSUFFICIENT_EVIDENCE: 1 },
        }],
      };
      if (path === '/api/v1/teacher/student-risk-overview') return {
        total: 2,
        items: [
          { id: 4, student_id: 'ST-004', full_name: 'Student Four', section: 'A', course_name: 'Biology 101', risk_category: 'HIGH', indicators: ['Recorded high risk outcome.'], subjects: [{ fa1: 11, fa1_observed_at: '2026-10-01T09:00:00Z', temporal_evidence_verified: true, attendance_rate: 82 }] },
          { id: 5, student_id: 'ST-005', full_name: 'Student Five', section: 'A', course_name: 'Biology 101', risk_category: 'INSUFFICIENT_EVIDENCE', indicators: ['No dated FA-1 mark is available.'], subjects: [{ fa1: 11, fa1_observed_at: null, temporal_evidence_verified: false, attendance_rate: null }] },
        ],
      };
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows configured evidence rules and routes into the selected subject', async () => {
    render(<TeacherRecoveryPage />);
    expect(await screen.findByRole('heading', { name: 'Subject support overview' })).toBeTruthy();
    expect(await screen.findByText(/strictly below 12\/25/)).toBeTruthy();
    const subjectLink = await screen.findByRole('link', { name: /Open subject/ });
    expect(subjectLink.getAttribute('href')).toBe('/teacher/recovery/subjects/12');
    const subjectCard = subjectLink.closest('article');
    expect(subjectCard).toBeTruthy();
    expect(within(subjectCard as HTMLElement).getAllByText('1', { selector: 'dd' })).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Assigned students' })).toBeTruthy();
    const directory = screen.getByRole('region', { name: 'Assigned student recovery directory' });
    const profileLink = within(directory).getAllByRole('link', { name: 'Open recovery profile' })
      .find((link) => link.getAttribute('href') === '/teacher/recovery/students/4');
    if (!profileLink) throw new Error('Student Four recovery profile link was not rendered.');
    expect(profileLink.getAttribute('href')).toBe('/teacher/recovery/students/4');
    expect(within(profileLink.closest('article') as HTMLElement).getAllByText('1', { selector: 'dd' })).toHaveLength(4);
    expect(within(profileLink.closest('article') as HTMLElement).getByText('Verified dates')).toBeTruthy();
  });

  it('renders every assigned student and filters the directory without a fixed count cap', async () => {
    const items = Array.from({ length: 8 }, (_, index) => ({
      id: 40 + index,
      student_id: `ST-${40 + index}`,
      full_name: `Recovery Student ${index + 1}`,
      section: 'A',
      course_name: 'Biology 101',
      risk_category: 'MEDIUM',
      indicators: ['Attendance evidence requires review.'],
      subjects: [],
    }));
    mocks.api.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/v1/teacher/subject-focus')) return {
        evidence_scope: 'FIRST_INTERNAL',
        policy: { version: 1, threshold_score: 12, threshold_maximum: 25, strictly_below: true, active_for_classification: true },
        subjects: [],
      };
      if (path === '/api/v1/teacher/student-risk-overview') return { total: items.length, items };
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<TeacherRecoveryPage />);

    expect(await screen.findAllByRole('link', { name: 'Open recovery profile' })).toHaveLength(8);
    expect(screen.getByText('Showing 8 of 8 assigned students')).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search assigned students' }), { target: { value: 'Recovery Student 3' } });
    expect(await screen.findAllByRole('link', { name: 'Open recovery profile' })).toHaveLength(1);
    expect(screen.getByText('Showing 1 of 8 assigned students')).toBeTruthy();
  });
});

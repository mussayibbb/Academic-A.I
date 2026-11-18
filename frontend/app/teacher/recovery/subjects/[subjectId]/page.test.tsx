// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeacherSubjectRosterPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));
vi.mock('@/components/SubjectModuleRecoveryPanel', () => ({
  default: ({ studentId, initialSubjectId }: { studentId?: number; initialSubjectId?: number }) => (
    <section aria-label="Subject recovery modules" data-student-id={studentId} data-subject-id={initialSubjectId} />
  ),
}));
vi.mock('next/navigation', () => ({ useParams: () => ({ subjectId: '12' }) }));

describe('teacher subject roster', () => {
  beforeEach(() => {
    mocks.api.mockResolvedValue({
      policy: { version: 1, threshold_score: 12, threshold_maximum: 25, strictly_below: true, active_for_classification: true },
      subjects: [{
        id: 12,
        code: 'BIO-101',
        name: 'Biology',
        counts: { LOW: 0, NOT_LOW: 0, INSUFFICIENT_EVIDENCE: 1 },
        students: [{
          id: 4,
          student_id: 'S-004',
          full_name: 'Student Four',
          section: 'A',
          status: 'INSUFFICIENT_EVIDENCE',
          reason: 'The assessment maximum has not been verified.',
          latest_assessment: {
            assessment: 'FA-1',
            score: 55,
            maximum_marks: 100,
            normalized_score: null,
            recorded_at: '2026-10-01T09:00:00',
            official_result: null,
          },
          official_failure: null,
          support_triggers: [],
        }],
      }],
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders unverified assessment evidence without formatting a missing normalized score', async () => {
    render(<TeacherSubjectRosterPage />);

    expect(await screen.findByText('Student Four')).toBeTruthy();
    expect(screen.getByText(/55 \/ 100 · normalized Insufficient evidence/)).toBeTruthy();
    expect(screen.getByText('The assessment maximum has not been verified.')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Subject recovery modules' })).toBeTruthy();
  });

  it('warns when the persisted policy still uses the retired scale', async () => {
    mocks.api.mockResolvedValueOnce({
      policy: { version: 1, threshold_score: 12, threshold_maximum: 20, strictly_below: true, active_for_classification: false },
      subjects: [],
    });
    render(<TeacherSubjectRosterPage />);
    expect(await screen.findByRole('alert')).toBeTruthy();
  });

  it('preserves the selected student and subject IDs in the upload workflow', async () => {
    window.history.replaceState({}, '', '/teacher/recovery/subjects/12?student_id=4');
    render(<TeacherSubjectRosterPage />);

    const panel = await screen.findByRole('region', { name: 'Subject recovery modules' });
    expect(panel.getAttribute('data-student-id')).toBe('4');
    expect(panel.getAttribute('data-subject-id')).toBe('12');
    expect((await screen.findByRole('link', { name: 'Student recovery profile' })).getAttribute('href'))
      .toBe('/teacher/recovery/students/4');
    expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/teacher/subject-focus?subject_id=12&first_internal_only=true&student_id=4',
    );
  });
});

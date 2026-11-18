// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Student360Page from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));
vi.mock('next/navigation', () => ({ useParams: () => ({ studentId: '17' }) }));

describe('teacher Student 360 subject recovery entry', () => {
  beforeEach(() => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/17') return {
        student: {
          id: 17,
          student_id: 'ST-17',
          full_name: 'Sample Student',
          risk_level: 'LOW',
          risk_probability: 0.8,
          previous_gpa: 3.1,
          current_gpa: 3.3,
          attendance: 92,
          assessment: 80,
          assignments_completed: 4,
        },
        momentum: [],
        submissions: [],
        risk_history: [],
        academic_records: { subjects: {}, risk_scope: 'GLOBAL', academic_period: null, academic_periods: [] },
      };
      if (path === '/api/v1/teacher/subject-focus?student_id=17&first_internal_only=true') return {
        subjects: [
          {
            id: 8,
            code: 'BIO',
            name: 'Biology',
            students: [{
              id: 17,
              status: 'LOW',
              reason: 'Verified first-internal result is below 12/25.',
              official_failure: null,
              support_triggers: ['LOW_SCORE'],
            }],
          },
          {
            id: 9,
            code: 'CHEM',
            name: 'Chemistry',
            students: [{
              id: 17,
              status: 'NOT_LOW',
              reason: 'Verified first-internal result is not below 12/25.',
              official_failure: false,
              support_triggers: [],
            }],
          },
        ],
      };
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads verified subject triggers and links to the matching subject-student workspace', async () => {
    render(<Student360Page />);

    expect(await screen.findByRole('heading', { name: 'Subject recovery workspaces' })).toBeTruthy();
    expect((await screen.findByRole('link', { name: 'Recovery profile' })).getAttribute('href'))
      .toBe('/teacher/recovery/students/17');
    const workspaceLink = await screen.findByRole('link', { name: 'Open Biology workspace' });
    expect(workspaceLink.getAttribute('href')).toBe('/teacher/recovery/subjects/8/students/17');
    expect(screen.queryByRole('link', { name: 'Open Chemistry workspace' })).toBeNull();
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/teacher/subject-focus?student_id=17&first_internal_only=true',
    ));
  });
});

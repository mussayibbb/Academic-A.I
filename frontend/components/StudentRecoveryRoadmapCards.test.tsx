// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentRecoveryRoadmapCards from './StudentRecoveryRoadmapCards';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

const focus = {
  policy: { threshold_score: 12, threshold_maximum: 25, active_for_classification: true },
  subjects: [
    {
      id: 11,
      code: 'BIO',
      name: 'Biology',
      status: 'LOW',
      reason: 'Verified first internal result is below the subject support threshold.',
      latest_assessment: {
        assessment: 'FA-1',
        score: 10,
        maximum_marks: 25,
        normalized_score: 10,
        recorded_at: '2026-10-10T00:00:00',
      },
      official_failure: null,
      support_triggers: ['LOW_SCORE'],
    },
    {
      id: 12,
      code: 'CHEM',
      name: 'Chemistry',
      status: 'NOT_LOW',
      reason: 'Verified first internal result is not below the subject support threshold.',
      latest_assessment: null,
      official_failure: false,
      support_triggers: [],
    },
    {
      id: 13,
      code: 'MATH',
      name: 'Mathematics',
      status: 'NOT_LOW',
      reason: 'Official result is FAIL.',
      latest_assessment: null,
      official_failure: true,
      support_triggers: ['OFFICIAL_FAIL'],
    },
  ],
};

describe('student subject recovery roadmap cards', () => {
  beforeEach(() => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') return focus;
      if (path === '/api/v1/students/me/recovery-tasks') return [
        {
          id: 4,
          subject_id: 11,
          subject: 'Biology',
          title: 'Review Biology evidence',
          status: 'COMPLETED',
          progress_percent: 100,
          scheduled_date: '2026-10-22',
          scheduled_at: null,
          deadline: null,
        },
        {
          id: 5,
          subject_id: null,
          subject: 'Mathematics',
          title: 'Legacy unscoped task',
          status: 'AVAILABLE',
          progress_percent: 0,
          scheduled_date: null,
          scheduled_at: null,
          deadline: null,
        },
      ];
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows separate cards only for actual support triggers or subject-scoped published tasks', async () => {
    render(<StudentRecoveryRoadmapCards />);

    expect(await screen.findByRole('article', { name: 'Biology recovery roadmap' })).toBeTruthy();
    expect(screen.getByRole('article', { name: 'Mathematics recovery roadmap' })).toBeTruthy();
    expect(screen.queryByRole('article', { name: 'Chemistry recovery roadmap' })).toBeNull();
    expect(screen.getByText('1 of 1 complete')).toBeTruthy();
    expect(screen.getByText('Review Biology evidence')).toBeTruthy();
    expect(screen.getByText(/Oct 22, 2026|10\/22\/2026|22\/10\/2026/)).toBeTruthy();
    expect(screen.queryByText('Legacy unscoped task')).toBeNull();
    const biologyCard = screen.getByRole('article', { name: 'Biology recovery roadmap' });
    expect(within(biologyCard).getByRole('link', { name: 'Open subject workspace' }).getAttribute('href')).toBe('/student/subjects/11');
    expect(within(biologyCard).getByRole('link', { name: 'View tasks' }).getAttribute('href')).toBe('/student/recovery?subject_id=11');
  });

  it.each([1, 2, 4, 5, 10])('renders all %i eligible subjects with their own subject routes', async (subjectCount) => {
    const subjects = Array.from({ length: subjectCount }, (_, index) => ({
      id: 100 + index,
      code: `REC-${index + 1}`,
      name: `Recovery subject ${index + 1}`,
      status: 'LOW',
      reason: 'Verified FA-1 result is strictly below 12/25.',
      latest_assessment: {
        assessment: 'FA-1',
        score: 11,
        maximum_marks: 25,
        normalized_score: 11,
        recorded_at: '2026-10-10T00:00:00',
      },
      official_failure: null,
      support_triggers: ['LOW_SCORE'],
    }));
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') {
        return { policy: focus.policy, subjects };
      }
      if (path === '/api/v1/students/me/recovery-tasks') return [];
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<StudentRecoveryRoadmapCards />);

    const cards = await screen.findAllByRole('article');
    expect(cards).toHaveLength(subjectCount);
    subjects.forEach((subject) => {
      const card = screen.getByRole('article', { name: `${subject.name} recovery roadmap` });
      expect(within(card).getByRole('link', { name: 'Open subject workspace' }).getAttribute('href'))
        .toBe(`/student/subjects/${subject.id}`);
    });
  });

  it('keeps a subject roadmap visible for a published task without a score-based trigger', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') {
        return { policy: focus.policy, subjects: [focus.subjects[1]] };
      }
      if (path === '/api/v1/students/me/recovery-tasks') return [{
        id: 12,
        subject_id: 12,
        subject: 'Chemistry',
        title: 'Teacher-published Chemistry review',
        status: 'AVAILABLE',
        progress_percent: 0,
        scheduled_date: null,
        scheduled_at: null,
        deadline: null,
      }];
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<StudentRecoveryRoadmapCards />);

    const chemistryCard = await screen.findByRole('article', { name: 'Chemistry recovery roadmap' });
    expect(within(chemistryCard).getByText('Teacher-published support')).toBeTruthy();
    expect(within(chemistryCard).getByText('Teacher-published Chemistry review')).toBeTruthy();
    expect(within(chemistryCard).getByRole('link', { name: 'View tasks' }).getAttribute('href'))
      .toBe('/student/recovery?subject_id=12');
  });

  it('renders every persisted roadmap task without a fixed display cap', async () => {
    const subjectTasks = Array.from({ length: 7 }, (_, index) => ({
      id: 20 + index,
      subject_id: 11,
      subject: 'Biology',
      title: `Biology milestone ${index + 1}`,
      status: index === 0 ? 'COMPLETED' : 'AVAILABLE',
      progress_percent: index === 0 ? 100 : 0,
      scheduled_date: `2026-10-${String(22 + index).padStart(2, '0')}`,
      scheduled_at: null,
      deadline: null,
    }));
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') return focus;
      if (path === '/api/v1/students/me/recovery-tasks') return subjectTasks;
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<StudentRecoveryRoadmapCards />);

    const biologyCard = await screen.findByRole('article', { name: 'Biology recovery roadmap' });
    subjectTasks.forEach((task) => expect(within(biologyCard).getByText(task.title)).toBeTruthy());
    expect(within(biologyCard).queryByText(/more published tasks/)).toBeNull();
    expect(within(biologyCard).getByText('1 of 7 complete')).toBeTruthy();
  });

  it('reports task API failures without hiding verified eligibility', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') return focus;
      throw new Error('Recovery task service unavailable');
    });

    render(<StudentRecoveryRoadmapCards />);

    expect(await screen.findByRole('article', { name: 'Biology recovery roadmap' })).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Recovery task service unavailable/)).toBeTruthy());
    const biologyCard = screen.getByRole('article', { name: 'Biology recovery roadmap' });
    expect(within(biologyCard).getByText('Published task details could not be loaded.')).toBeTruthy();
    expect(within(biologyCard).queryByText('0 of 0 complete')).toBeNull();
  });
});

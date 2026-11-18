// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeacherRecoveryPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

const recoveryOverview = {
  stats: {
    students_with_backlogs: 1,
    backlog_subjects: 1,
    quizzes_assigned: 2,
    average_recovery_score: 75,
  },
  pass_mark: 12,
  maximum_marks: 25,
  subjects: [{
    id: 12,
    name: 'Biology',
    code: 'BIO-101',
    modules: [
      { module_no: 1, status: 'READY', filename: 'cell-biology.pdf', uploaded: true },
      { module_no: 2, status: 'READY', filename: '', uploaded: false },
      { module_no: 3, status: 'READY', filename: '', uploaded: false },
      { module_no: 4, status: 'READY', filename: '', uploaded: false },
      { module_no: 5, status: 'READY', filename: '', uploaded: false },
    ],
  }],
  students: [
    {
      id: 4,
      student_id: 'ST-004',
      name: 'Student Four',
      section: 'A',
      course_name: 'Biology 101',
      subjects: [{
        id: 12,
        name: 'Biology',
        code: 'BIO-101',
        fa1: 11,
        maximum: 25,
        academic_period: '2026-S1',
        backlog: true,
        evidence_source: 'subject_academic_record',
        plan: null,
      }],
    },
    {
      id: 5,
      student_id: 'ST-005',
      name: 'Student Five',
      section: 'B',
      course_name: 'Biology 101',
      subjects: [{
        id: 12,
        name: 'Biology',
        code: 'BIO-101',
        fa1: 18,
        maximum: 25,
        academic_period: '2026-S1',
        backlog: false,
        evidence_source: 'subject_academic_record',
        plan: null,
      }],
    },
  ],
};

describe('teacher subject recovery workspace', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/teacher/recovery/overview') return recoveryOverview;
      if (path === '/api/v1/teacher/recovery/students/4/performance') return {
        items: [{ date: '2026-11-17', score: 8, max_score: 10, percent: 80, attempts: 1 }],
      };
      if (path === '/api/v1/teacher/recovery/students/5/performance') return { items: [] };
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders database-backed statistics and a dated performance chart', async () => {
    render(<TeacherRecoveryPage />);
    expect(await screen.findByRole('heading', { name: 'Subject recovery' })).toBeTruthy();
    const stats = screen.getByRole('region', { name: 'Recovery statistics' });
    expect(within(stats).getByText('Students with backlogs').parentElement?.textContent).toContain('1');
    expect(within(stats).getByText('Quizzes assigned').parentElement?.textContent).toContain('2');
    expect(within(stats).getByText('Average recovery score').parentElement?.textContent).toContain('75%');
    const chartValue = await screen.findByText('80%');
    expect(chartValue.parentElement?.getAttribute('aria-label')).toContain('2026-11-17');
    expect(screen.getByText('Students in your classes')).toBeTruthy();
  });

  it('matches student name, ID, class and course', async () => {
    render(<TeacherRecoveryPage />);
    expect(await screen.findByRole('button', { name: /Student Four/ })).toBeTruthy();
    const search = screen.getByRole('searchbox', {
      name: 'Search students by name, ID, class or course',
    });
    expect(search).toBeTruthy();
    fireEvent.change(search, { target: { value: 'Class B' } });
    expect(screen.getByRole('button', { name: /Student Five/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Student Four/ })).toBeNull();
    fireEvent.change(search, { target: { value: 'ST-004' } });
    expect(screen.getByRole('button', { name: /Student Four/ })).toBeTruthy();
  });

  it('never preselects or enables modules without an uploaded PDF', async () => {
    render(<TeacherRecoveryPage />);
    const moduleChoices = await screen.findByRole('group', { name: 'Select uploaded modules' });
    expect(await screen.findByText('1/5 uploaded')).toBeTruthy();
    const available = within(moduleChoices).getByRole('checkbox', { name: 'Module 1' }) as HTMLInputElement;
    const missing = within(moduleChoices).getByRole('checkbox', { name: 'Module 2 (not uploaded)' }) as HTMLInputElement;
    expect(available.checked).toBe(false);
    expect(available.disabled).toBe(false);
    expect(missing.checked).toBe(false);
    expect(missing.disabled).toBe(true);
  });
});

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import InterventionsPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api, readCachedApiData: () => null }));

let beforeGenerate: ((studentId: number) => Promise<void>) | null = null;

const students = [
  { id: 1, full_name: 'Alex River', email: 'alex@example.edu', risk_level: 'HIGH', risk_probability: 0.82, attendance: 62, assignments_completed: 3 },
  { id: 2, full_name: 'Jordan Lee', email: null, risk_level: 'MEDIUM', risk_probability: 0.58, attendance: 74, assignments_completed: 5 },
  { id: 3, full_name: 'Casey Park', risk_level: 'HIGH', risk_probability: 0.76, attendance: 69, assignments_completed: 4 },
  { id: 4, full_name: 'Taylor Quinn', risk_level: 'LOW', risk_probability: 0.2, attendance: 93, assignments_completed: 8 },
  { id: 5, full_name: 'Quinn Stone', risk_level: 'HIGH', risk_probability: 0.71, attendance: 67, assignments_completed: 2 },
];

const makeDrafts = () => [{
  id: 10,
  student_id: 1,
  title: 'Support check-in',
  body: [
    'AI-GENERATED DRAFT - FACULTY REVIEW REQUIRED',
    'Subject: Attendance check-in',
    'Hi Alex,',
    'Your recorded attendance is 62%. Could we review your course schedule together?',
    'Best,',
    'Your faculty member',
    'No verified institutional resource is currently configured.',
  ].join('\n'),
  status: 'draft',
  provider: 'ollama-local',
  evidence: {
    risk_prediction: { risk_level: 'HIGH', risk_probability: 0.82, input_features: { attendance: 62, assignments_completed: 3 } },
    momentum: { assessment: 'STABLE' },
    shap: { target_class: 'HIGH' },
    rag: { sources: [] },
  },
}];

let draftRecords: ReturnType<typeof makeDrafts>;

beforeEach(() => {
  draftRecords = makeDrafts();
  beforeGenerate = null;
  mocks.api.mockReset();
  mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/v1/students?limit=100') return { items: students, total: students.length };
    if (path === '/api/v1/interventions') return [...draftRecords];
    if (/^\/api\/v1\/students\/\d+\/academic-records$/.test(path)) {
      return {
        subjects: {
          SE: { name: 'Software Engineering', fa1_normalized: 8 },
          CN: { name: 'Computer Networks', fa1_normalized: 14 },
        },
      };
    }
    if (path === '/api/v1/interventions/generate') {
      const { student_id: studentId } = JSON.parse(String(init?.body)) as { student_id: number };
      if (beforeGenerate) await beforeGenerate(studentId);
      const student = students.find((item) => item.id === studentId);
      const created = {
        id: 20 + studentId,
        student_id: studentId,
        title: 'Support check-in',
        body: `${'AI-GENERATED DRAFT - FACULTY REVIEW REQUIRED'}\nSubject: A check-in\nHi ${student?.full_name ?? 'student'},\nCould we meet to review one next step?\nBest,\nYour faculty member\nNo verified institutional resource is currently configured.`,
        status: 'draft',
        provider: 'ollama-local',
        evidence: {
          risk_prediction: {
            risk_level: student?.risk_level ?? 'UNKNOWN',
            risk_probability: student?.risk_probability ?? 0,
            input_features: {
              attendance: student?.attendance ?? 0,
              assignments_completed: student?.assignments_completed ?? 0,
            },
          },
          momentum: { assessment: 'STABLE' },
          shap: { target_class: student?.risk_level ?? 'UNKNOWN' },
          rag: { sources: [] },
        },
      };
      draftRecords = [created, ...draftRecords];
      return created;
    }
    if (/^\/api\/v1\/interventions\/\d+$/.test(path) && init?.method === 'PATCH') {
      const id = Number(path.split('/').pop());
      const value = JSON.parse(String(init.body)) as { title: string; body: string };
      draftRecords = draftRecords.map((item) => item.id === id ? { ...item, ...value } : item);
      return { id, ...value, status: 'draft' };
    }
    if (/^\/api\/v1\/interventions\/\d+\/approve$/.test(path)) {
      const id = Number(path.split('/')[4]);
      draftRecords = draftRecords.map((item) => item.id === id ? { ...item, status: 'approved' } : item);
      return { status: 'approved' };
    }
    throw new Error(`Unexpected API request: ${path}`);
  });
});

afterEach(() => cleanup());

describe('teacher interventions page', () => {
  it('filters and searches real student records, loads marks on selection, and generates only from the star', async () => {
    render(<InterventionsPage />);

    expect(await screen.findByRole('button', { name: 'Select student Alex River' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Select student Taylor Quinn' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Draft already open for Alex River' })).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort students' }), { target: { value: 'name' } });
    expect(screen.getAllByRole('button', { name: /Select student/ }).map((button) => button.getAttribute('aria-label'))).toEqual([
      'Select student Alex River',
      'Select student Casey Park',
      'Select student Jordan Lee',
      'Select student Quinn Stone',
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Select student Jordan Lee' }));
    expect(await screen.findByText(/Software Engineering \(8\.0\/25\)/)).toBeTruthy();
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/students/2/academic-records');

    fireEvent.click(screen.getByRole('button', { name: 'High risk' }));
    expect(screen.getByRole('button', { name: 'Select student Alex River' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Select student Jordan Lee' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search students by name' }), { target: { value: 'casey' } });
    expect(screen.getByRole('button', { name: 'Select student Casey Park' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Select student Alex River' })).toBeNull();
    expect(mocks.api).not.toHaveBeenCalledWith('/api/v1/interventions/generate', expect.anything());

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search students by name' }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate draft for Casey Park' }));
    await waitFor(() => {
      expect(mocks.api).toHaveBeenCalledWith('/api/v1/interventions/generate', {
        method: 'POST',
        body: JSON.stringify({ student_id: 3 }),
      });
    });
    expect(await screen.findByText(/Draft created for Casey Park/)).toBeTruthy();
  });

  it('saves edits, displays returned evidence, and approves an in-app notification', async () => {
    render(<InterventionsPage />);

    const subject = await screen.findByRole('textbox', { name: 'Subject' });
    fireEvent.change(subject, { target: { value: 'Faculty check-in' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Message body/ }), {
      target: { value: 'Could we meet to review your recorded attendance?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save edits' }));
    await waitFor(() => {
      expect(mocks.api).toHaveBeenCalledWith('/api/v1/interventions/10', {
        method: 'PATCH',
        body: JSON.stringify({ title: 'Faculty check-in', body: 'Could we meet to review your recorded attendance?' }),
      });
    });
    expect(await screen.findByText('Faculty edits saved.')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'See the evidence behind this draft' }));
    expect(await screen.findByText('Lowest verified FA-1 marks')).toBeTruthy();
    expect(await screen.findByText(/Software Engineering: 8\.0\/25/)).toBeTruthy();
    expect(screen.getByText('alex@example.edu')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Approve and notify student' }));
    await waitFor(() => {
      expect(mocks.api).toHaveBeenCalledWith('/api/v1/interventions/10/approve', { method: 'POST' });
    });
    fireEvent.click(screen.getByRole('tab', { name: /Sent/ }));
    expect(await screen.findByText(/ollama-local · Approved/)).toBeTruthy();
  });

  it('locally dismisses a draft without calling a delete endpoint', async () => {
    render(<InterventionsPage />);
    await screen.findByRole('button', { name: 'Discard' });

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(await screen.findByText(/hidden for this session/)).toBeTruthy();
    expect(mocks.api.mock.calls.some(([path]) => String(path).endsWith('/10/delete'))).toBe(false);
  });

  it('creates high-risk drafts sequentially and skips students with an open draft', async () => {
    let activeRequests = 0;
    let maxConcurrentRequests = 0;
    const requestOrder: number[] = [];
    beforeGenerate = async (studentId) => {
      activeRequests += 1;
      maxConcurrentRequests = Math.max(maxConcurrentRequests, activeRequests);
      requestOrder.push(studentId);
      await new Promise((resolve) => window.setTimeout(resolve, 5));
      activeRequests -= 1;
    };
    render(<InterventionsPage />);

    await screen.findByRole('button', { name: 'Select student Alex River' });
    fireEvent.click(screen.getByRole('button', { name: 'Draft for all high-risk students' }));
    await waitFor(() => {
      expect(mocks.api.mock.calls.filter(([path]) => path === '/api/v1/interventions/generate')).toHaveLength(2);
    });
    expect(requestOrder).toEqual([3, 5]);
    expect(maxConcurrentRequests).toBe(1);
    expect(await screen.findByText(/2 drafts created/)).toBeTruthy();
  });

  it('shows a retry action when loading the student directory fails', async () => {
    mocks.api.mockImplementationOnce(async () => { throw new Error('Request failed (500)'); });
    render(<InterventionsPage />);

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/Loading students failed with a server error/)).toBeTruthy();
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('button', { name: 'Select student Alex River' })).toBeTruthy();
  });
});

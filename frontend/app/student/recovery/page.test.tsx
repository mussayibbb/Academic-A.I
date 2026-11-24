// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentRecoveryPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api, readCachedApiData: () => null }));

const task = {
  id: 7,
  roadmap_version_id: 3,
  student_id: 4,
  subject_id: 12,
  task_key: 'bio-evidence',
  subject: 'Biology',
  topic: null,
  title: 'Review recorded academic evidence: Biology',
  learning_objective: 'Review the recorded academic evidence with your teacher.',
  instructions: 'Review the assessment record with your teacher.',
  activity_type: 'TEACHER_REVIEW',
  priority: 'MEDIUM',
  source: 'EVIDENCE_REVIEW',
  scheduled_at: null,
  deadline: null,
  estimated_minutes: 20,
  resources: [],
  completion_criteria: 'Record the discussion with your teacher.',
  mastery_criteria: null,
  status: 'AVAILABLE',
  progress_percent: 0,
  student_response: null,
  submitted_at: null,
  assessed_score: null,
  teacher_feedback: null,
  revision_count: 0,
  completed_at: null,
  evidence_refs: ['mark:9'],
  evidence: [{
    ref: 'mark:9',
    kind: 'assessment_mark',
    subject: 'Biology',
    assessment: 'FA1',
    score: 45,
    maximum_marks: 100,
    recorded_at: '2026-08-10T00:00:00',
  }],
  field_provenance: { estimated_minutes: 'PROPOSED_DEFAULT_REQUIRES_TEACHER_REVIEW' },
  activity: {
    reason: 'Review the verified Biology assessment with your teacher.',
    lesson: 'This is a general evidence-reading skill, not a verified lesson from the course.',
    steps: ['Write an observation.', 'Name a limit.', 'Ask a question.'],
    exercise: 'Write an Observation, a Limit, and a Question for your teacher.',
    submission_format: 'Submit a short written reflection.',
  },
};

describe('student recovery tasks page', () => {
  let currentTask: Omit<typeof task, 'student_response'> & { student_response: string | null };
  let tutorResponse: {
    status: 'answered' | 'unsupported' | 'no_evidence' | 'unavailable';
    answer: string;
    practice_suggestions: string[];
    evidence_refs: string[];
    evidence: typeof task.evidence;
    missing_information: string[];
    provider: string | null;
    answer_source: 'provider' | 'built_in' | 'deterministic_fallback';
    answer_scope: 'GENERAL_EXPLANATION' | 'STUDENT_EVIDENCE' | null;
    document_retrieval: 'disabled_unscoped_index';
  };
  let tutorFailure = false;
  let tutorDelay = false;
  let additionalTasks: typeof task[] = [];

  beforeEach(() => {
    currentTask = { ...task };
    additionalTasks = [];
    tutorResponse = {
      status: 'answered',
      answer: 'Your recorded Biology assessment is available for review.',
      practice_suggestions: ['Review this result with your teacher.'],
      evidence_refs: ['mark:9'],
      evidence: task.evidence,
      missing_information: [],
      provider: 'test-provider',
      answer_source: 'provider',
      answer_scope: 'STUDENT_EVIDENCE',
      document_retrieval: 'disabled_unscoped_index',
    };
    tutorFailure = false;
    tutorDelay = false;
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') {
        return { policy: { threshold_score: 12, threshold_maximum: 25, active_for_classification: true }, subjects: [] };
      }
      if (path === '/api/v1/students/me/topic-learning-assignments') return [];
      if (path === '/api/v1/students/me/recovery-tasks') return [currentTask, ...additionalTasks];
      if (path === '/api/v1/students/me/recovery-timeline') return { items: [] };
      if (path === '/api/v1/students/me/recovery-tutor' && init?.method === 'POST') {
        if (tutorFailure) throw new Error('Tutor service unavailable');
        if (tutorDelay) await new Promise((resolve) => setTimeout(resolve, 80));
        return tutorResponse;
      }
      if (path === '/api/v1/recovery-tasks/7' && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { action: 'start' | 'progress'; progress_percent?: number };
        currentTask = {
          ...currentTask,
          status: 'IN_PROGRESS',
          ...(body.action === 'progress' && body.progress_percent !== undefined ? { progress_percent: body.progress_percent } : {}),
        };
        return currentTask;
      }
      if (path === '/api/v1/recovery-tasks/7/draft' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { response: string };
        currentTask = { ...currentTask, student_response: body.response };
        return currentTask;
      }
      if (path === '/api/v1/recovery-tasks/7/submit' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { response: string };
        currentTask = { ...currentTask, status: 'SUBMITTED', student_response: body.response };
        return currentTask;
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('starts an assigned task and submits student work through the recovery API', async () => {
    render(<StudentRecoveryPage />);
    expect(await screen.findByRole('heading', { name: task.title })).toBeTruthy();
    expect(screen.getByText('FA1: 45 / 100')).toBeTruthy();
    expect(screen.getByText(/Start the learning activity/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Start task/ }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-tasks/7',
      expect.objectContaining({ method: 'PATCH' }),
    ));
    expect(screen.getByRole('heading', { name: 'Worked example using the linked record' })).toBeTruthy();
    expect(screen.getByText(/does not identify which topic or concept needs more practice/)).toBeTruthy();
    expect(screen.getByText(/General evidence-study skill · not a verified course lesson/)).toBeTruthy();
    expect(await screen.findByLabelText('Your work or reflection')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Your work or reflection'), {
      target: { value: 'Reviewed the recorded assessment with my teacher.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-tasks/7/draft',
      expect.objectContaining({ method: 'PUT' }),
    ));
    expect((currentTask as typeof task).student_response).toBe('Reviewed the recorded assessment with my teacher.');
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-tasks/7/submit',
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByText('Submitted for teacher review')).toBeTruthy();
  });

  it('filters published tasks by the persisted subject identifier', async () => {
    window.history.replaceState({}, '', '/student/recovery?subject_id=12');
    render(<StudentRecoveryPage />);

    expect(await screen.findByRole('heading', { name: 'Biology recovery tasks' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: task.title })).toBeTruthy();
  });

  it('summarizes progress from every persisted task on the student dashboard', async () => {
    currentTask = { ...task, status: 'COMPLETED', progress_percent: 100 };
    additionalTasks = [
      { ...task, id: 8, status: 'IN_PROGRESS', progress_percent: 40, title: 'Continue Biology practice' },
      { ...task, id: 9, status: 'SUBMITTED', progress_percent: 75, title: 'Submit Biology reflection' },
    ];
    render(<StudentRecoveryPage />);

    const progressBar = await screen.findByRole('progressbar', { name: 'Overall recovery task progress' });
    expect(progressBar.getAttribute('aria-valuenow')).toBe('72');
    expect(screen.getByText('Persisted across all recovery subjects')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Submit Biology reflection' })).toBeTruthy();
  });

  it('renders verified eligible subject cards on the actual Recovery route', async () => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/subject-focus?first_internal_only=true') {
        return {
          policy: { threshold_score: 12, threshold_maximum: 25, active_for_classification: true },
          subjects: [{
            id: 12,
            code: 'BIO-101',
            name: 'Biology',
            status: 'LOW',
            reason: 'Verified FA-1 result is strictly below 12/25.',
            latest_assessment: {
              assessment: 'FA-1',
              score: 11,
              maximum_marks: 25,
              normalized_score: 11,
              recorded_at: '2026-08-10T00:00:00',
            },
            official_failure: null,
            support_triggers: ['LOW_SCORE'],
          }],
        };
      }
      if (path === '/api/v1/students/me/recovery-tasks') return [currentTask];
      if (path === '/api/v1/students/me/topic-learning-assignments') return [];
      if (path === '/api/v1/students/me/recovery-timeline') return { items: [] };
      throw new Error(`Unexpected API call: ${path}`);
    });

    render(<StudentRecoveryPage />);

    const card = await screen.findByRole('article', { name: 'Biology recovery roadmap' });
    expect(card.textContent).toContain('Verified FA-1 result is strictly below 12/25.');
    expect(screen.getByRole('link', { name: 'Open subject workspace' }).getAttribute('href')).toBe('/student/subjects/12');
  });

  it.each([1, 3, 5, 12])(
    'renders all %i subject-specific tasks in a synthetic multi-subject roadmap',
    async (subjectCount) => {
      additionalTasks = Array.from({ length: subjectCount - 1 }, (_, index) => ({
        ...task,
        id: 20 + index,
        subject_id: 20 + index,
        subject: `Synthetic subject ${index + 1}`,
        title: `Synthetic recovery task ${index + 1}`,
      }));
      render(<StudentRecoveryPage />);

      for (let index = 1; index < subjectCount; index += 1) {
        expect(await screen.findByRole('heading', { name: `Synthetic recovery task ${index}` })).toBeTruthy();
      }
      expect(await screen.findByRole('heading', { name: task.title })).toBeTruthy();
    },
  );

  it('restores and saves persisted progress for an in-progress task', async () => {
    currentTask = { ...task, status: 'IN_PROGRESS', progress_percent: 35 };
    render(<StudentRecoveryPage />);

    const progressInput = await screen.findByLabelText(/Progress/);
    expect((progressInput as HTMLInputElement).value).toBe('35');
    fireEvent.change(progressInput, { target: { value: '65' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save progress' }));

    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-tasks/7',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.stringContaining('"progress_percent":65'),
      }),
    ));
    await waitFor(() => expect(currentTask.progress_percent).toBe(65));
    expect(screen.getByRole('progressbar', { name: 'Overall recovery task progress' }).getAttribute('aria-valuenow')).toBe('65');
    expect(screen.getByRole('status').textContent).toContain('Your progress was saved.');
  });

  it('keeps an empty subject-ID filter scoped to that subject', async () => {
    window.history.replaceState({}, '', '/student/recovery?subject_id=99');
    render(<StudentRecoveryPage />);

    expect(await screen.findByText('No tasks for this subject yet')).toBeTruthy();
  });

  it('submits a tutor question and displays the real response and cited evidence', async () => {
    render(<StudentRecoveryPage />);
    expect(await screen.findByRole('heading', { name: 'Recovery AI tutor' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Your question'), {
      target: { value: 'What does my recorded Biology assessment show?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask tutor' }));

    expect(await screen.findByText(tutorResponse.answer)).toBeTruthy();
    expect(screen.getByText(/AI explanation of verified academic records/)).toBeTruthy();
    expect(screen.getByText('Review this result with your teacher.')).toBeTruthy();
    expect(screen.getAllByText('FA1: 45 / 100').length).toBeGreaterThanOrEqual(2);
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/students/me/recovery-tutor',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ question: 'What does my recorded Biology assessment show?', conversation_history: [] }),
      }),
    ));
    expect(screen.getByText(/AI explanation of verified academic records/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Ask a follow-up' }));
    expect((screen.getByLabelText('Your question') as HTMLTextAreaElement).value).toBe('Can you explain your previous answer with a different example?');
  });

  it('shows a clear no-evidence state from the tutor endpoint', async () => {
    tutorResponse = {
      ...tutorResponse,
      status: 'no_evidence',
      answer: 'There is not enough verified academic evidence to answer this question.',
      practice_suggestions: [],
      evidence_refs: [],
      evidence: [],
    };
    render(<StudentRecoveryPage />);
    fireEvent.change(await screen.findByLabelText('Your question'), {
      target: { value: 'What is my current progress?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask tutor' }));

    expect(await screen.findByText('There is not enough verified academic evidence to answer this question.')).toBeTruthy();
    expect(screen.getByText('No verified academic evidence was available for this question.')).toBeTruthy();
  });

  it('shows a loading state while the tutor request is pending', async () => {
    tutorDelay = true;
    render(<StudentRecoveryPage />);
    fireEvent.change(await screen.findByLabelText('Your question'), {
      target: { value: 'Explain a recorded result.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask tutor' }));

    expect(screen.getByRole('button', { name: /Thinking/ })).toBeTruthy();
    expect(await screen.findByText(tutorResponse.answer)).toBeTruthy();
  });

  it('shows tutor endpoint errors without hiding the recovery task page', async () => {
    tutorFailure = true;
    render(<StudentRecoveryPage />);
    fireEvent.change(await screen.findByLabelText('Your question'), {
      target: { value: 'Please explain my record.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask tutor' }));

    expect(await screen.findByText('Tutor service unavailable')).toBeTruthy();
    expect(screen.getByRole('heading', { name: task.title })).toBeTruthy();
  });
});

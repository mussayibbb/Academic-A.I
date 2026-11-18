// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SubjectModuleRecoveryPanel from './SubjectModuleRecoveryPanel';
import StudentLearningRecoveryPanel from './StudentLearningRecoveryPanel';

const mocks = vi.hoisted(() => ({ api: vi.fn(), token: 'teacher-token' }));
vi.mock('@/lib/api', () => ({ API_BASE: 'http://localhost:8000', api: mocks.api, readToken: () => mocks.token }));

type MockResource = Record<string, unknown> & { id: number; provenance: Record<string, unknown> };
let resourceRows: MockResource[];
let topicRows: Record<string, unknown>[];
let studySession: Record<string, unknown> | null;
let publishedAssessment: Record<string, unknown> | null;

class UploadXhrMock {
  static sentFiles: string[] = [];
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  status = 0;
  responseText = '';
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private file: File | null = null;
  open() {}
  setRequestHeader() {}
  send(form: FormData) {
    this.file = form.get('file') as File;
    queueMicrotask(() => {
      const file = this.file as File;
      UploadXhrMock.sentFiles.push(file.name);
      this.upload.onprogress?.({ lengthComputable: true, loaded: file.size, total: file.size } as ProgressEvent);
      const resource = {
        id: resourceRows.length + 1, module_id: 11, subject_id: 4, title: file.name.replace(/\.[^.]+$/, ''),
        original_filename: file.name, file_type: file.name.split('.').pop()?.toUpperCase(), file_size: file.size,
        content_hash: `hash-${resourceRows.length + 1}`, processing_status: 'PROCESSED', error_message: null,
        extracted_text: 'Cells contain genetic material.',
        provenance: { source: 'teacher_upload' }, created_at: '2026-10-16T10:00:00',
      };
      resourceRows = [resource, ...resourceRows];
      this.status = 200;
      this.responseText = JSON.stringify(resource);
      this.onload?.();
    });
  }
}

const modules = [{ id: 11, subject_id: 4, module_number: 1, title: 'Cell biology', description: null, status: 'ACTIVE', resource_count: 0 }];
const questionSet = Array.from({ length: 20 }, (_, index) => ({
  id: index + 1,
  question_type: index < 15 ? 'MCQ' as const : 'SHORT_ANSWER' as const,
  question: `Question ${index + 1} about the published source?`,
  options: index < 15 ? ['Option A', 'Option B', 'Option C', 'Option D'] : [],
  marks: 1,
  learning_objective: 'Describe the approved topic',
  rubric: {},
  source_evidence: [],
}));

describe('Recovery learning panels', () => {
  beforeEach(() => {
    resourceRows = [];
    topicRows = [];
    studySession = null;
    publishedAssessment = null;
    UploadXhrMock.sentFiles = [];
    mocks.token = 'teacher-token';
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v1/subjects') return [{ id: 4, code: 'BIO-200', name: 'Biology' }];
      if (path === '/api/v1/subjects/4/modules') return modules;
      if (path === '/api/v1/subjects/4/topics' || path === '/api/v1/subjects/4/topics/approve') {
        if (init?.method === 'POST') {
          const body = JSON.parse(String(init.body)) as { resource_id: number; candidate_id: string; title: string; summary: string; source_order: number; order_uncertain: boolean };
          const topic = { id: 21, module_id: 11, title: body.title, summary: body.summary, learning_objectives: [], concepts: [], approval_status: 'APPROVED', source_provenance: { resource_id: body.resource_id, candidate_id: body.candidate_id, source_order: body.source_order, order_uncertain: body.order_uncertain } };
          topicRows = [topic];
          return topicRows;
        }
        return topicRows;
      }
      if (path === '/api/v1/subjects/4/modules/11/resources') return resourceRows;
      if (/^\/api\/v1\/subjects\/4\/modules\/11\/resources\/\d+\/extract-topics$/.test(path) && init?.method === 'POST') {
        const resourceId = Number(path.match(/resources\/(\d+)\/extract-topics$/)?.[1]);
        const resourceIndex = resourceRows.findIndex((item) => item.id === resourceId);
        const resource = {
          ...resourceRows[resourceIndex],
          extracted_topics: [{
            candidate_id: '1', candidate_path: [1], original_number: '3.1', source_order: 1,
            title: 'Cell structure', summary: 'Cells contain and organize genetic material.',
            learning_objectives: ['Describe cell structure'], concepts: ['cells'],
            source_excerpt: 'Cells contain genetic material.', source_locator: null,
            order_uncertain: false, hierarchy_uncertain: true, requires_review: true,
            review_reason: 'Confirm whether the listed point is a subtopic or a separate topic.',
            children: [], course_title: 'Biology 200', module_number: 1, module_title: 'Cell biology',
          }],
          provenance: {
            ...resourceRows[resourceIndex].provenance, topic_map_status: 'REVIEW_REQUIRED',
            topic_map_metadata: { course_title: 'Biology 200', module_number: 1, module_title: 'Cell biology', source_filename: 'cells.txt' },
          },
        };
        resourceRows = resourceRows.map((item, index) => index === resourceIndex ? resource : item);
        return resource;
      }
      if (path === '/api/v1/students/7/topic-learning-assignments') return [];
      if (path === '/api/v1/students/me/topic-learning-assignments') return [{
        id: 50, subject_id: 4, subject_name: 'Biology', module_id: 11, module_number: 1, module_title: 'Cell biology',
        approved_topic_id: 21, topic_title: 'Cell structure', topic_summary: 'Approved cell biology content.', title: 'Cell structure',
        learning_objective: 'Describe cell structure', lesson_content: 'Study the approved cells resource.', expected_duration_minutes: 30,
        status: 'ACTIVE', study_session: studySession, assessment: publishedAssessment, latest_attempt: null,
        attempt_count: 0, resources: [],
      }];
      if (path === '/api/v1/topic-learning-assignments/50/study-session' && init?.method === 'POST') {
        studySession = { id: 5, started_at: '2026-10-16T10:00:00', required_duration_minutes: 30, elapsed_seconds: 0, remaining_seconds: 1800, completed_at: null, ready: false };
        return studySession;
      }
      if (path === '/api/v1/topic-learning-assignments/50/attempts' && init?.method === 'POST') {
        return { id: 80, status: 'IN_PROGRESS', question_set: { questions: questionSet } };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
    vi.stubGlobal('XMLHttpRequest', UploadXhrMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('uploads multiple real-contract resources and approves the extracted topic in Recovery', async () => {
    render(<SubjectModuleRecoveryPanel studentId={7} />);
    expect(await screen.findByRole('combobox', { name: 'Subject recovery track' })).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Module 5' })).toBeTruthy();
    const uploadInput = await screen.findByLabelText('Add or replace with newer source files');
    fireEvent.change(uploadInput, { target: { files: [
      new File(['cell content one'], 'cells.txt', { type: 'text/plain' }),
      new File(['cell content two'], 'cell-notes.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }),
    ] } });
    fireEvent.click(screen.getByRole('button', { name: 'Upload 2' }));
    expect(await screen.findByText('PROCESSED')).toBeTruthy();
    await waitFor(() => expect(UploadXhrMock.sentFiles).toHaveLength(2));
    expect(UploadXhrMock.sentFiles).toEqual(['cells.txt', 'cell-notes.docx']);
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/subjects/4/modules/11/resources/1/extract-topics',
      { method: 'POST' },
    ));
    expect(screen.getAllByText(/Cell structure/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Confirm whether the listed point/).length).toBeGreaterThan(0);

    fireEvent.change(screen.getAllByLabelText('Teacher-reviewed summary')[0], { target: { value: 'Cells contain and organize genetic material.' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Approve topic' })[0]);
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/api/v1/subjects/4/topics/approve', expect.objectContaining({
      method: 'POST',
      body: expect.stringContaining('"candidate_id":"1"'),
    })));
    const approvalCall = mocks.api.mock.calls.find(([path, init]) => path === '/api/v1/subjects/4/topics/approve' && init?.method === 'POST');
    expect(approvalCall?.[1]).toEqual(expect.objectContaining({
      body: expect.stringContaining('"source_order":1'),
    }));
    expect(await screen.findByText(/Approved .*Cell structure/)).toBeTruthy();
  });

  it.each([undefined, 0])(
    'lets teachers manage modules and review topics before selecting a student (studentId=%s)',
    async (studentId) => {
      render(<SubjectModuleRecoveryPanel {...(studentId === undefined ? {} : { studentId })} initialSubjectId={4} />);

      expect(await screen.findByRole('heading', { name: 'Module 5' })).toBeTruthy();
      expect(screen.getByRole('heading', { name: 'Modules, assessments and progress' })).toBeTruthy();
      expect(screen.getAllByLabelText('Upload source files (creates this module slot if needed)')).toHaveLength(4);
      expect(mocks.api).not.toHaveBeenCalledWith(
        expect.stringMatching(/\/students\/(?:undefined|0)\/topic-learning-assignments/),
        expect.anything(),
      );
      expect(screen.queryByRole('button', { name: /Assign to student/ })).toBeNull();
    },
  );

  it('starts a persisted server timer and leaves assessment locked before elapsed duration', async () => {
    mocks.token = 'student-token';
    render(<StudentLearningRecoveryPanel />);
    expect(await screen.findByRole('heading', { name: 'Cell structure' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Start study timer' }));
    expect(await screen.findByText(/^30:0[01]$/)).toBeTruthy();
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/api/v1/topic-learning-assignments/50/study-session', expect.objectContaining({ method: 'POST' })));
    expect(screen.queryByRole('button', { name: /Start published assessment/ })).toBeNull();
    expect(screen.getByText(/not attention or comprehension/)).toBeTruthy();
  });

  it('only offers a published assessment after the server has completed the required timer', async () => {
    mocks.token = 'student-token';
    studySession = { id: 5, started_at: '2026-10-16T09:00:00', required_duration_minutes: 30, elapsed_seconds: 1800, remaining_seconds: 0, completed_at: '2026-10-16T09:30:00', ready: true };
    publishedAssessment = { id: 9, version: 1, status: 'PUBLISHED', question_count: 20, questions: questionSet };
    render(<StudentLearningRecoveryPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Start published assessment/ }));
    expect(await screen.findByText(/Question 1 about the published source/)).toBeTruthy();
    expect(screen.getAllByRole('group').filter((element) => element.tagName === 'FIELDSET')).toHaveLength(20);
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/api/v1/topic-learning-assignments/50/attempts', expect.objectContaining({ method: 'POST' })));
  });
});
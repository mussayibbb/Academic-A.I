// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeacherRecoveryPage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

const student = { id: 4, student_id: 'S-004', full_name: 'Student Four', section: 'A' };
const task = {
  task_key: 'bio-review',
  title: 'Review Biology evidence',
  learning_objective: 'Review recorded Biology evidence with a teacher.',
  instructions: 'Discuss the assessment record and identify topic-level follow-up.',
  activity_type: 'TEACHER_REVIEW',
  priority: 'MEDIUM' as const,
  subject: 'Biology',
  topic: null,
  scheduled_at: null,
  deadline: null,
  estimated_minutes: 20,
  resources: [],
  completion_criteria: 'Record the discussion.',
  mastery_criteria: null,
  evidence_refs: ['mark:9'],
  activity: {
    reason: 'Review the verified Biology record with a teacher.',
    lesson: 'A recorded result describes an assessment, not topic mastery.',
    steps: ['Review the record.', 'Ask a question about follow-up.'],
    exercise: 'Write one observation and one question.',
    submission_format: 'Submit a short written response.',
  },
  field_provenance: { subject: 'VERIFIED_ACADEMIC_RECORD' },
  status: 'AVAILABLE',
  source: 'EVIDENCE_REVIEW',
};
const monitoringTask = {
  ...task,
  id: 7,
  student_id: student.id,
  subject_id: 40,
  student_name: student.full_name,
  student_code: student.student_id,
  progress_percent: 0,
  student_response: null,
  activity: task.activity,
  pending_activity: null,
  evidence: [{ ref: 'mark:9', kind: 'assessment_mark', subject: 'Biology' }],
};

describe('teacher recovery roadmap builder page', () => {
  let currentPlan: {
    id: number;
    student_id: number;
    version: number;
    status: 'DRAFT' | 'APPROVED' | 'PUBLISHED';
    summary: string;
    plan: {
      tasks: typeof task[];
      blueprint?: {
        status: string;
        generation_status: string;
        academic_period: string | null;
        exam_date: string;
        available_minutes_per_day: number;
        syllabus: { context: string | null; source: string | null; status: string; reviewed: boolean };
        subjects: {
          subject_id: number;
          subject_name: string;
          focus_reason: string;
          eligibility: {
            eligible: boolean;
            status: string;
            support_triggers: string[];
            official_failure_status: string | null;
            reason: string;
            latest_evidence: { evidence_id: string; [key: string]: unknown }[];
          };
          evidence_source: string;
          fa1_evidence: {
            academic_period: string | null;
            period_linkage: string;
            records: {
              evidence_id: string;
              mark_id: number | null;
              assessment: string;
              score: number | null;
              maximum_marks: number | null;
              maximum_marks_verified: boolean;
              normalized_score: number | null;
              recorded_at: string | null;
              official_result: string | null;
              evidence_status: string;
              reason: string | null;
              source_filenames: string[];
            }[];
          };
          topics: [];
        }[];
        schedule: [];
        blockers: string[];
      };
    };
    evidence: {
      academic_records: { ref: string; kind: string; subject: string }[];
      model_outputs: { risk_prediction: null };
      missing_information: string[];
      generation: Record<string, string>;
    };
  } | null;
  let published = false;
  let focusSubjectCount = 1;
  let overdueTest = false;
  let subjectScopeTest = false;

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    currentPlan = null;
    published = false;
    focusSubjectCount = 1;
    overdueTest = false;
    subjectScopeTest = false;
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v1/subjects') return [];
      if (path === '/api/v1/teacher/recovery-monitoring') {
        return {
          counts: { AVAILABLE: published ? 1 : 0 },
          tasks: subjectScopeTest
            ? [
              monitoringTask,
              { ...monitoringTask, id: 8, subject_id: 41, subject: 'Chemistry', title: 'Review Chemistry evidence' },
            ]
            : overdueTest ? [{ ...monitoringTask, status: 'IN_PROGRESS', deadline: '2000-01-01T00:00:00Z' }] : published ? [monitoringTask] : [],
          students: [student],
        };
      }
      if (path.startsWith('/api/v1/teacher/student-risk-overview')) {
        return {
          academic_period: '2026-S1',
          academic_periods: ['2026-S1'],
          items: [{
            id: student.id,
            subjects: [{
              id: 40, code: 'BIO-101', name: 'Biology',
              fa1: 14, fa1_observed_at: '2026-08-10',
              fa2: null, fa2_observed_at: null,
              assignment_mark: null, assignment_observed_at: null,
              attendance_rate: null, attendance_observed_at: null,
            }],
          }],
        };
      }
      if (path.startsWith('/api/v1/teacher/subject-focus?')) {
        return {
          subjects: Array.from({ length: focusSubjectCount }, (_, index) => ({
            id: 40 + index,
            code: index === 0 ? 'BIO-101' : `SYN-${index}`,
            name: index === 0 ? 'Biology' : `Synthetic subject ${index}`,
            students: [{
              id: student.id,
              status: 'LOW' as const,
              reason: 'Verified synthetic FA-1 result is strictly below 12/25.',
              support_triggers: ['LOW_SCORE'],
              official_failure_status: null,
              latest_assessments: [{
                evidence_id: `mark:${9 + index}`,
                mark_id: 9 + index,
                student_id: student.id,
                assessment_id: 3 + index,
                assessment: `SYNTHETIC TEST FA-1 ${index}`,
                score: 11,
                maximum_marks: 25,
                maximum_marks_verified: true,
                normalized_score: 11,
                recorded_at: '2026-08-10T00:00:00',
                official_result: null,
                evidence_status: 'VERIFIED',
                reason: null,
                source_filenames: [`synthetic-fa1-${index}.csv`],
              }],
            }],
          })),
        };
      }
      if (/^\/api\/v1\/subjects\/\d+\/modules$/.test(path)) return [];
      if (/^\/api\/v1\/subjects\/\d+\/topics$/.test(path)) return [];
      if (/^\/api\/v1\/subjects\/\d+\/modules\/\d+\/resources$/.test(path)) return [];
      if (path === `/api/v1/students/${student.id}/topic-learning-assignments`) return [];
      if (path === `/api/v1/students/${student.id}/recovery-blueprints` && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { academic_period: string | null; exam_date: string };
        currentPlan = {
          id: 13,
          student_id: student.id,
          version: 2,
          status: 'DRAFT',
          summary: 'Teacher review required.',
          plan: {
            tasks: [],
            blueprint: {
              status: 'DRAFT',
              generation_status: 'GENERATED_NEEDS_TEACHER_REVIEW',
              academic_period: body.academic_period,
              exam_date: body.exam_date,
              available_minutes_per_day: 60,
              syllabus: { context: null, source: null, status: 'NO_OFFICIAL_MAPPING_PROVIDED', reviewed: false },
              subjects: [{
                subject_id: 40,
                subject_name: 'Biology',
                focus_reason: 'Teacher-confirmed focus; recorded FA-1 14.',
                eligibility: {
                  eligible: true,
                  status: 'LOW',
                  support_triggers: ['LOW_SCORE'],
                  official_failure_status: null,
                  reason: 'Verified FA-1 result is strictly below 12/25.',
                  latest_evidence: [{
                    evidence_id: 'mark:9',
                    mark_id: 9,
                    student_id: student.id,
                    assessment_id: 3,
                    assessment: 'FA-1 Biology',
                    score: 14,
                    maximum_marks: 25,
                    maximum_marks_verified: true,
                    normalized_score: 14,
                    recorded_at: '2026-08-10T00:00:00',
                    official_result: null,
                    evidence_status: 'VERIFIED',
                    reason: null,
                    source_filenames: ['biology-fa1.csv'],
                  }],
                },
                evidence_source: 'DATED_FIRST_INTERNAL_ASSESSMENT_MARKS',
                fa1_evidence: {
                  academic_period: '2026-S1',
                  period_linkage: 'ACADEMIC_RECORD_LINKED',
                  records: [{
                    evidence_id: 'mark:9',
                    mark_id: 9,
                    assessment: 'FA-1 Biology',
                    score: 14,
                    maximum_marks: 25,
                    maximum_marks_verified: true,
                    normalized_score: 14,
                    recorded_at: '2026-08-10T00:00:00',
                    official_result: null,
                    evidence_status: 'VERIFIED',
                    reason: null,
                    source_filenames: ['biology-fa1.csv'],
                  }],
                },
                topics: [],
              }],
              schedule: [],
              blockers: ['No teacher-approved topics are available yet.'],
            },
          },
          evidence: {
            academic_records: [{ ref: 'record:5', kind: 'student_subject_academic_record', subject: 'Biology' }],
            model_outputs: { risk_prediction: null },
            missing_information: [],
            generation: {},
          },
        };
        return currentPlan;
      }
      if (path === `/api/v1/students/${student.id}/recovery-plans` && init?.method === 'POST') {
        currentPlan = {
          id: 12,
          student_id: student.id,
          version: 1,
          status: 'DRAFT',
          summary: 'Evidence-linked review task is ready for teacher review.',
          plan: { tasks: [{ ...task }] },
          evidence: {
            academic_records: [{ ref: 'mark:9', kind: 'assessment_mark', subject: 'Biology' }],
            model_outputs: { risk_prediction: null },
            missing_information: ['Topic-level evidence is unavailable.'],
            generation: { generator: 'deterministic_evidence_rules' },
          },
        };
        return currentPlan;
      }
      if (path === `/api/v1/students/${student.id}/recovery-plans`) {
        return currentPlan ? [currentPlan] : [];
      }
      if (path === `/api/v1/students/${student.id}/topic-learning-assignments`) return [];
      if (path === `/api/v1/recovery-plans/12` && init?.method === 'PATCH' && currentPlan) {
        const body = JSON.parse(String(init.body)) as { summary: string; tasks: typeof task[] };
        currentPlan = { ...currentPlan, summary: body.summary, plan: { tasks: body.tasks } };
        return currentPlan;
      }
      if (path === '/api/v1/recovery-plans/12/approve' && init?.method === 'POST' && currentPlan) {
        currentPlan = { ...currentPlan, status: 'APPROVED' };
        return currentPlan;
      }
      if (path === '/api/v1/recovery-plans/12/publish' && init?.method === 'POST' && currentPlan) {
        currentPlan = { ...currentPlan, status: 'PUBLISHED' };
        published = true;
        return currentPlan;
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, '', '/');
    vi.clearAllMocks();
  });

  it('limits a subject-specific roadmap to the selected student and subject', async () => {
    subjectScopeTest = true;
    window.history.replaceState({}, '', '/teacher/recovery/roadmaps?student_id=4&subject_id=40');
    render(<TeacherRecoveryPage />);

    expect(await screen.findByText('Individual Subject Roadmap · Biology · Student Student Four')).toBeTruthy();
    expect(await screen.findByRole('heading', { name: 'Review Biology evidence' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Review Chemistry evidence' })).toBeNull();
  });

  it('shows overdue students with links preserving their student and subject identifiers', async () => {
    overdueTest = true;
    render(<TeacherRecoveryPage />);

    expect(await screen.findByRole('heading', { name: 'Stalled recovery work' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Recovery profile' }).getAttribute('href'))
      .toBe('/teacher/recovery/students/4');
    expect(screen.getByRole('link', { name: 'Upload Modules' }).getAttribute('href'))
      .toBe('/teacher/recovery/subjects/40/students/4');
    expect(screen.getByRole('link', { name: 'Individual Roadmap' }).getAttribute('href'))
      .toBe('/teacher/recovery/roadmaps?student_id=4&subject_id=40');
  });

  it('generates, approves, and publishes a reviewed roadmap', async () => {
    render(<TeacherRecoveryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Generate evidence review plan' }));
    await screen.findByDisplayValue('Evidence-linked review task is ready for teacher review.');

    fireEvent.click(screen.getByRole('button', { name: 'Save draft edits' }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-plans/12',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.stringContaining('"activity":{"reason":"Review the verified Biology record with a teacher."'),
      }),
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-plans/12/approve',
      expect.objectContaining({ method: 'POST' }),
    ));
    fireEvent.click(await screen.findByRole('button', { name: /Publish roadmap/ }));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/recovery-plans/12/publish',
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByText(/Published to Student Four/)).toBeTruthy();
    expect(screen.getByText('Student Four · S-004 · Biology')).toBeTruthy();
  });

  it('loads authoritative FA-1 evidence and requests an all-subject blueprint without a subject list', async () => {
    render(<TeacherRecoveryPage />);
    fireEvent.click(await screen.findByRole('checkbox', { name: /BIO-101 · Biology/ }));
    fireEvent.change(screen.getByLabelText('Official FA-2 exam date'), { target: { value: '2026-11-10' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /No official syllabus mapping is currently available/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Generate AI roadmap blueprint' }));

    await screen.findByText(/Proposed blueprint · teacher review required/);
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/teacher/subject-focus?student_id=4&first_internal_only=true',
    ));
    await waitFor(() => expect(mocks.api).toHaveBeenCalledWith(
      `/api/v1/students/${student.id}/recovery-blueprints`,
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"acknowledge_no_syllabus":true'),
      }),
    ));
    const blueprintRequest = mocks.api.mock.calls.find(
      ([path, init]) => path === `/api/v1/students/${student.id}/recovery-blueprints` && init?.method === 'POST',
    );
    expect(blueprintRequest).toBeDefined();
    expect(JSON.parse(String(blueprintRequest?.[1]?.body))).not.toHaveProperty('subject_ids');
    expect(await screen.findByText(/Mark 9/)).toBeTruthy();
    expect(screen.getAllByText(/biology-fa1.csv/).length).toBeGreaterThan(0);
    expect(screen.getByText(/No teacher-approved topics are available yet/)).toBeTruthy();
  });

  it.each([1, 3, 5, 12])(
    'renders all %i synthetic eligible subject evidence cards',
    async (subjectCount) => {
      focusSubjectCount = subjectCount;
      render(<TeacherRecoveryPage />);
      const evidenceRegion = screen.getByRole('region', { name: 'First-internal eligibility evidence' });

      for (let index = 0; index < subjectCount; index += 1) {
        const subjectName = index === 0 ? 'BIO-101 · Biology' : `SYN-${index} · Synthetic subject ${index}`;
        expect(await within(evidenceRegion).findByText(subjectName)).toBeTruthy();
      }
      expect(within(evidenceRegion).getAllByText(/Eligible: below 12\/25/)).toHaveLength(subjectCount);
    },
  );
});

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TeacherRecoveryStudentProfilePage from './page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));
vi.mock('next/navigation', () => ({ useParams: () => ({ studentId: '17' }) }));

const evidence = (
  normalizedScore: number,
  options: {
    maximumVerified?: boolean;
    maximumMarks?: number;
    score?: number;
    recordedAt?: string | null;
    status?: string;
    temporalVerified?: boolean;
    evidenceId?: string;
    academicPeriod?: string | null;
  } = {},
) => ({
  evidence_id: options.evidenceId ?? `assessment:${normalizedScore}`,
  assessment: 'FA-1',
  score: options.score ?? normalizedScore,
  maximum_marks: options.maximumVerified === false ? null : (options.maximumMarks ?? 25),
  maximum_marks_verified: options.maximumVerified !== false,
  normalized_score: options.maximumVerified === false || options.status === 'INSUFFICIENT_EVIDENCE' ? null : normalizedScore,
  evidence_status: options.status ?? (options.maximumVerified === false ? 'INSUFFICIENT_EVIDENCE' : 'VERIFIED'),
  temporal_evidence_verified: options.temporalVerified ?? true,
  recorded_at: options.recordedAt === undefined ? '2026-10-10T09:00:00' : options.recordedAt,
  source: 'Recorded assessment',
  academic_period: options.academicPeriod === undefined ? '2026-S1' : options.academicPeriod,
  official_result: null,
  reason: options.maximumVerified === false ? 'The assessment maximum has not been verified from an explicit source.' : null,
});

const academicRecord = (
  record_id: number,
  subject_id: number,
  code: string,
  name: string,
  fa1: number | null,
  options: {
    date?: string | null;
    temporalVerified?: boolean;
    maximumMarks?: number | null;
    maximumVerified?: boolean;
    importBatchId?: number | null;
    sourceFilename?: string | null;
  } = {},
) => ({
  record_id,
  student_id: 17,
  subject_id,
  code,
  name,
  academic_period: '2026-S1' as const,
  fa1,
  fa1_maximum_marks: options.maximumMarks === undefined ? 25 : options.maximumMarks,
  fa1_maximum_verified: options.maximumVerified ?? true,
  fa1_observed_at: options.date === undefined ? '2026-10-10T09:00:00' : options.date,
  temporal_evidence_verified: options.temporalVerified ?? true,
  import_batch_id: options.importBatchId === undefined ? record_id : options.importBatchId,
  source_filename: options.sourceFilename === undefined ? 'semester-source.xlsx' : options.sourceFilename,
});

describe('teacher recovery student profile', () => {
  beforeEach(() => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/teacher/subject-focus?student_id=17&first_internal_only=true') return {
        evidence_scope: 'FIRST_INTERNAL',
        policy: { version: 2, threshold_score: 12, threshold_maximum: 25, strictly_below: true, active_for_classification: true },
        subjects: [
          {
            id: 12, code: 'BIO', name: 'Biology',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'LOW', reason: 'Verified FA-1 result is below threshold.',
              latest_assessment: evidence(11, { score: 22, maximumMarks: 50, evidenceId: 'academic-record:101:fa1' }),
              latest_assessments: [evidence(11, { score: 22, maximumMarks: 50, evidenceId: 'academic-record:101:fa1' })],
              history: [evidence(11, { score: 22, maximumMarks: 50, evidenceId: 'academic-record:101:fa1' })],
              official_failure: false, support_triggers: ['LOW_SCORE'],
              attendance_rate: 86, attendance_observed_at: '2026-10-08T09:00:00',
            }],
          },
          {
            id: 13, code: 'CHEM', name: 'Chemistry',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'NOT_LOW', reason: 'At threshold.',
              latest_assessment: evidence(12, { evidenceId: 'academic-record:102:fa1' }),
              latest_assessments: [evidence(12, { evidenceId: 'academic-record:102:fa1' })],
              history: [evidence(12, { evidenceId: 'academic-record:102:fa1' })],
              official_failure: false, support_triggers: [],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
          {
            id: 14, code: 'MATH', name: 'Mathematics',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'INSUFFICIENT_EVIDENCE', reason: 'No verified mark.',
              latest_assessment: null,
              latest_assessments: [evidence(8, { maximumVerified: false, recordedAt: null, evidenceId: 'academic-record:103:fa1' })],
              history: [evidence(8, { maximumVerified: false, recordedAt: null, evidenceId: 'academic-record:103:fa1' })],
              official_failure: true, support_triggers: ['OFFICIAL_FAIL'],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
          {
            id: 15, code: 'PHYS', name: 'Physics',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'LOW', reason: 'Verified FA-1 result is below threshold.',
              latest_assessment: evidence(10, { evidenceId: 'academic-record:104:fa1' }),
              latest_assessments: [evidence(10, { evidenceId: 'academic-record:104:fa1' })],
              history: [evidence(10, { evidenceId: 'academic-record:104:fa1' })],
              official_failure: false, support_triggers: ['LOW_SCORE'],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
          {
            id: 16, code: 'HIST', name: 'History',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'INSUFFICIENT_EVIDENCE', reason: 'The assessment maximum has not been verified.',
              latest_assessment: evidence(9, { maximumVerified: false, evidenceId: 'academic-record:105:fa1' }),
              latest_assessments: [evidence(9, { maximumVerified: false, evidenceId: 'academic-record:105:fa1' })],
              history: [evidence(9, { maximumVerified: false, evidenceId: 'academic-record:105:fa1' })],
              official_failure: false, support_triggers: [],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
          {
            id: 17, code: 'ART', name: 'Arts',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'INSUFFICIENT_EVIDENCE', reason: 'The assessment date provenance is missing.',
              latest_assessment: evidence(9, { status: 'INSUFFICIENT_EVIDENCE', temporalVerified: false, evidenceId: 'academic-record:106:fa1' }),
              latest_assessments: [evidence(9, { status: 'INSUFFICIENT_EVIDENCE', temporalVerified: false, evidenceId: 'academic-record:106:fa1' })],
              history: [evidence(9, { status: 'INSUFFICIENT_EVIDENCE', temporalVerified: false, evidenceId: 'academic-record:106:fa1' })],
              official_failure: false, support_triggers: [],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
          {
            id: 18, code: 'MUSIC', name: 'Music',
            students: [{
              id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
              status: 'INSUFFICIENT_EVIDENCE', reason: 'No FA-1 value is recorded.',
              latest_assessment: null, latest_assessments: [], history: [], official_failure: false, support_triggers: [],
              attendance_rate: null, attendance_observed_at: null,
            }],
          },
        ],
      };
      if (path === '/api/v1/teacher/student-risk-overview?student_id=17') return {
        academic_period: '2026-S1',
        total: 1,
        items: [{
          id: 17, student_id: 'S-017', full_name: 'Student Seventeen', section: 'A',
          course_name: 'Biology 101', risk_category: 'HIGH',
          risk_outcome: { risk_level: 'HIGH', observed_at: '2026-10-08T09:00:00', outcome_source: 'Imported register', academic_period: '2026-S1' },
          indicators: ['Recorded high risk outcome from Imported register.'],
        }],
      };
      if (path === '/api/v1/students/17/academic-records?academic_period=2026-S1') return {
        academic_period: '2026-S1',
        records: [
          academicRecord(101, 12, 'BIO', 'Biology', 22, { maximumMarks: 50 }),
          academicRecord(102, 13, 'CHEM', 'Chemistry', 12),
          academicRecord(103, 14, 'MATH', 'Mathematics', 8, { date: null, temporalVerified: false }),
          academicRecord(104, 15, 'PHYS', 'Physics', 10),
          academicRecord(105, 16, 'HIST', 'History', 9, { maximumVerified: false }),
          academicRecord(106, 17, 'ART', 'Arts', 9, { temporalVerified: false }),
          academicRecord(107, 18, 'MUSIC', 'Music', null, {
            date: null, temporalVerified: false, importBatchId: null, sourceFilename: null,
          }),
        ],
      };
      if (path === '/api/v1/students/17/topic-learning-assignments') return [{
        id: 30, subject_id: 12, module_id: 120, module_number: 3, approved_topic_id: 301,
        topic_title: 'Cell transport', title: 'Cell transport', status: 'MASTERED',
        expected_duration_minutes: 30, attempt_count: 1,
        assessment: { id: 40, version: 1, status: 'PUBLISHED', question_count: 20 },
        latest_attempt: { id: 50, status: 'GRADED', percentage: 85, review_pending: false },
      }];
      if (path === '/api/v1/subjects/12/modules' || path === '/api/v1/subjects/15/modules') return [
        { id: Number(path.match(/subjects\/(\d+)/)?.[1]) * 10, module_number: 3, title: 'Cell systems', status: 'ACTIVE', resource_count: 2 },
        { id: Number(path.match(/subjects\/(\d+)/)?.[1]) * 10 + 1, module_number: 4, title: 'Genetics', status: 'ACTIVE', resource_count: 1 },
      ];
      if (path === '/api/v1/subjects/12/topics') return [
        { id: 301, module_id: 120, title: 'Cell transport', approval_status: 'APPROVED' },
        { id: 302, module_id: 120, title: 'Cell signalling', approval_status: 'APPROVED' },
        { id: 401, module_id: 121, title: 'Genetic inheritance', approval_status: 'DRAFT' },
      ];
      if (path === '/api/v1/subjects/15/topics') return [];
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('renders every evidence record and keeps authorized module review separate from eligibility', async () => {
    render(<TeacherRecoveryStudentProfilePage />);

    expect(await screen.findByRole('heading', { name: 'Student Seventeen' })).toBeTruthy();
    const evidenceReview = await screen.findByRole('region', { name: 'Subject-wise FA-1 Evidence' });
    expect(within(evidenceReview).getAllByRole('link', { name: /Review .* modules/ })).toHaveLength(7);
    expect(within(evidenceReview).getByRole('link', { name: 'Review Chemistry modules' }).getAttribute('href'))
      .toBe('/teacher/recovery/subjects/13/students/17');
    const evidenceDetails = evidenceReview.querySelector('details');
    expect(evidenceDetails).not.toBeNull();
    fireEvent.click(within(evidenceReview).getByText('Subject-wise FA-1 Evidence'));
    const evidenceRows = within(evidenceDetails as HTMLElement);
    expect(evidenceRows.getAllByRole('listitem')).toHaveLength(7);
    expect(evidenceRows.getByText(/BIO · Biology · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/CHEM · Chemistry · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/MATH · Mathematics · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/PHYS · Physics · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/HIST · History · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/ART · Arts · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText(/MUSIC · Music · FA-1/)).toBeTruthy();
    expect(evidenceRows.getByText('INSUFFICIENT EVIDENCE — DATE MISSING')).toBeTruthy();
    expect(evidenceRows.getByText('INSUFFICIENT EVIDENCE — SCORE SCALE UNVERIFIED')).toBeTruthy();
    expect(evidenceRows.getByText('INSUFFICIENT EVIDENCE — SOURCE/VERIFICATION MISSING')).toBeTruthy();
    expect(evidenceRows.getByText('INSUFFICIENT EVIDENCE — FA-1 MARK MISSING')).toBeTruthy();
    expect(evidenceRows.getByText('NOT ELIGIBLE — SCORE AT OR ABOVE 12/25')).toBeTruthy();
    expect(evidenceRows.getAllByText('ELIGIBLE FOR RECOVERY')).toHaveLength(2);
    expect(evidenceRows.getAllByText(/semester-source\.xlsx/)).toHaveLength(6);
    expect(evidenceRows.getByText('Import reference unavailable')).toBeTruthy();
    expect(evidenceRows.getAllByText(/Subject record: 2026-S1/)).toHaveLength(7);

    const biology = await screen.findByRole('article', { name: 'Biology recovery progress' });
    const physics = await screen.findByRole('article', { name: 'Physics recovery progress' });
    expect(screen.queryByRole('article', { name: 'Chemistry recovery progress' })).toBeNull();
    expect(within(biology).getAllByText(/Cell transport/).length).toBeGreaterThan(0);
    expect(within(biology).getAllByText(/Cell signalling/).length).toBeGreaterThan(0);
    expect(within(biology).getByText('1 of 2 approved')).toBeTruthy();
    expect(within(biology).getByText(/assessment published \(20 questions\)/)).toBeTruthy();
    expect(screen.getAllByText(/Mathematics/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Separate official FAIL support/)).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Subject-wise FA-1 Evidence' })).toBeTruthy();
    expect(within(biology).getByRole('link', { name: /Upload Modules/ }).getAttribute('href'))
      .toBe('/teacher/recovery/subjects/12/students/17');
    expect(within(biology).getByRole('link', { name: /Individual Subject Roadmap/ }).getAttribute('href'))
      .toBe('/teacher/recovery/roadmaps?student_id=17&subject_id=12');
    expect(within(physics).getByRole('link', { name: /Upload Modules/ }).getAttribute('href'))
      .toBe('/teacher/recovery/subjects/15/students/17');
    expect(within(physics).getByRole('link', { name: /Individual Subject Roadmap/ }).getAttribute('href'))
      .toBe('/teacher/recovery/roadmaps?student_id=17&subject_id=15');
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/students/17/academic-records?academic_period=2026-S1');
    await waitFor(() => expect(within(biology).getByText(/awaiting teacher approval/)).toBeTruthy());
  });

  it('explains unavailable workspaces for evidence subjects outside the assigned scope', async () => {
    const originalApi = mocks.api.getMockImplementation();
    mocks.api.mockImplementation(async (path: string) => {
      const result = await originalApi?.(path);
      if (path !== '/api/v1/teacher/subject-focus?student_id=17&first_internal_only=true' || !result) return result;
      return { ...result, subjects: result.subjects.filter((subject: { id: number }) => subject.id === 12) };
    });

    render(<TeacherRecoveryStudentProfilePage />);

    const evidenceReview = await screen.findByRole('region', { name: 'Subject-wise FA-1 Evidence' });
    expect(within(evidenceReview).getByRole('heading', { name: 'Teacher module review' })).toBeTruthy();
    expect(within(evidenceReview).getAllByRole('link', { name: /Review .* modules/ })).toHaveLength(1);
    expect(within(evidenceReview).getAllByText('Workspace unavailable — subject is outside your assigned teaching scope.')).toHaveLength(6);
  });
});

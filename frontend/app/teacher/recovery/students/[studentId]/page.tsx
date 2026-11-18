'use client';

import { ArrowLeft, ArrowRight, BookOpenCheck, CalendarCheck2, CircleAlert, ShieldCheck } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading, RiskBadge } from '@/components/ui';
import { api } from '@/lib/api';
import { strictRecoveryEligibleSubjects, type StrictRecoveryFocus } from '@/lib/recovery-eligibility';

type FocusAssessment = {
  evidence_id: string;
  assessment: string;
  score: number | null;
  maximum_marks: number | null;
  maximum_marks_verified: boolean;
  normalized_score: number | null;
  evidence_status: string;
  temporal_evidence_verified: boolean | null;
  recorded_at: string | null;
  source: string;
  academic_period: string | null;
  official_result: string | null;
  reason: string | null;
};
type FocusStudent = {
  id: number;
  student_id: string;
  full_name: string;
  section: string | null;
  status: string;
  reason: string;
  latest_assessment: FocusAssessment | null;
  latest_assessments: FocusAssessment[];
  history: FocusAssessment[];
  official_failure: boolean | null;
  support_triggers: string[];
  attendance_rate: number | null;
  attendance_observed_at: string | null;
};
type FocusSubject = {
  id: number;
  code: string;
  name: string;
  students: FocusStudent[];
};
type FocusResponse = {
  evidence_scope: string;
  policy: {
    version: number;
    threshold_score: number;
    threshold_maximum: number;
    strictly_below: boolean;
    active_for_classification: boolean;
  } | null;
  subjects: FocusSubject[];
};
type RiskStudent = {
  id: number;
  student_id: string;
  full_name: string;
  section: string | null;
  course_name: string | null;
  risk_category: string;
  academic_period?: string | null;
  risk_outcome: { risk_level: string; observed_at: string; outcome_source: string; academic_period: string } | null;
  indicators: string[];
};
type RiskOverview = { academic_period?: string | null; items: RiskStudent[]; total: number };
type AcademicRecord = {
  record_id: number;
  student_id: number;
  subject_id: number;
  code: string;
  name: string;
  academic_period: string;
  fa1: number | null;
  fa1_observed_at: string | null;
  temporal_evidence_verified: boolean;
  fa1_maximum_marks: number | null;
  fa1_maximum_verified: boolean;
  fa1_maximum_source_reference: string | null;
  import_batch_id: number | null;
  source_filename: string | null;
};
type AcademicRecords = { academic_period: string | null; records: AcademicRecord[] };
type Module = { id: number; module_number: number; title: string; status: string; resource_count: number };
type Topic = { id: number; module_id: number; title: string; approval_status: string };
type Assignment = {
  id: number;
  subject_id: number;
  module_id: number;
  module_number: number | null;
  approved_topic_id: number;
  topic_title: string;
  title: string;
  status: string;
  expected_duration_minutes: number;
  assessment: { id: number; version: number; status: string; question_count: number } | null;
  latest_attempt: { id: number; status: string; percentage: number | null; review_pending: boolean } | null;
  attempt_count: number;
};

function dateLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Date not recorded';
}

function eligibleSubjectsForStudent(
  focus: FocusResponse | null,
  records: AcademicRecord[],
  studentId: number,
): FocusSubject[] {
  return strictRecoveryEligibleSubjects(focus, studentId).filter((subject) => {
    const record = records.find((item) => item.subject_id === subject.id && item.fa1 !== null);
    const student = subject.students.find((item) => item.id === studentId);
    const assessment = record && student?.history.find(
      (item) => item.evidence_id === `academic-record:${record.record_id}:fa1`,
    );
    return Boolean(
      record
      && record.fa1_observed_at
      && record.temporal_evidence_verified
      && record.fa1_maximum_verified
      && record.fa1_maximum_marks !== null
      && assessment
      && assessment.evidence_status === 'VERIFIED'
      && assessment.maximum_marks_verified
      && assessment.maximum_marks === record.fa1_maximum_marks
      && assessment.score === record.fa1
      && assessment.normalized_score !== null
      && assessment.normalized_score < 12
      && assessment.recorded_at === record.fa1_observed_at
      && assessment.academic_period === record.academic_period,
    );
  });
}

function evidenceState(
  record: AcademicRecord,
  assessment: FocusAssessment | null,
  eligible: boolean,
): { label: string; reason: string } {
  if (eligible) {
    return {
      label: 'ELIGIBLE FOR RECOVERY',
      reason: 'Verified first-internal evidence is dated, has a verified maximum, and is strictly below 12/25.',
    };
  }
  if (record.fa1 === null) {
    return {
      label: 'INSUFFICIENT EVIDENCE — FA-1 MARK MISSING',
      reason: 'This Internal Marks subject record does not contain an FA-1 value.',
    };
  }
  if (!record.fa1_observed_at) {
    return {
      label: 'INSUFFICIENT EVIDENCE — DATE MISSING',
      reason: 'The Internal Marks FA-1 record has no recorded assessment date.',
    };
  }
  if (!record.temporal_evidence_verified) {
    return {
      label: 'INSUFFICIENT EVIDENCE — SOURCE/VERIFICATION MISSING',
      reason: 'The recorded FA-1 date has not been temporally verified.',
    };
  }
  if (!assessment) {
    return {
      label: 'INSUFFICIENT EVIDENCE — SOURCE/VERIFICATION MISSING',
      reason: 'No subject-focus evidence is linked to this Internal Marks record identifier.',
    };
  }
  if (
    assessment.score !== record.fa1
    || assessment.recorded_at !== record.fa1_observed_at
    || assessment.academic_period !== record.academic_period
  ) {
    return {
      label: 'INSUFFICIENT EVIDENCE — CONFLICTING FA-1 RECORDS',
      reason: 'The date, period, or mark in subject-focus evidence does not exactly match this Internal Marks record.',
    };
  }
  if (
    !assessment.maximum_marks_verified
    || !record.fa1_maximum_verified
    || record.fa1_maximum_marks === null
    || assessment.maximum_marks !== record.fa1_maximum_marks
    || assessment.evidence_status !== 'VERIFIED'
  ) {
    return {
      label: 'INSUFFICIENT EVIDENCE — SCORE SCALE UNVERIFIED',
      reason: assessment.reason ?? 'No verified 25-mark maximum is linked to this Internal Marks FA-1 record.',
    };
  }
  if (assessment.normalized_score === null) {
    return {
      label: 'INSUFFICIENT EVIDENCE — EVIDENCE INVALID OR AMBIGUOUS',
      reason: assessment.reason ?? 'The available FA-1 evidence cannot be normalized safely.',
    };
  }
  if (assessment.normalized_score >= 12) {
    return {
      label: 'NOT ELIGIBLE — SCORE AT OR ABOVE 12/25',
      reason: 'The verified normalized first-internal score is at or above the strict 12/25 threshold.',
    };
  }
  return {
    label: 'INSUFFICIENT EVIDENCE — SOURCE/VERIFICATION MISSING',
    reason: assessment.reason ?? 'The available FA-1 evidence does not meet all verification requirements.',
  };
}

export default function TeacherRecoveryStudentProfilePage() {
  const params = useParams<{ studentId: string }>();
  const studentId = Number(params.studentId);
  const [focus, setFocus] = useState<FocusResponse | null>(null);
  const [riskStudent, setRiskStudent] = useState<RiskStudent | null>(null);
  const [academicRecords, setAcademicRecords] = useState<AcademicRecord[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [subjectResources, setSubjectResources] = useState<Record<number, { modules: Module[]; topics: Topic[] }>>({});
  const [sourceReferences, setSourceReferences] = useState<Record<number, string>>({});
  const [reviewingRecordId, setReviewingRecordId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isInteger(studentId) || studentId <= 0) {
      setError('Invalid student identifier.');
      setLoading(false);
      return;
    }
    let active = true;
    const query = new URLSearchParams({ student_id: String(studentId), first_internal_only: 'true' });
    Promise.all([
      api<FocusResponse>(`/api/v1/teacher/subject-focus?${query}`),
      api<RiskOverview>(`/api/v1/teacher/student-risk-overview?student_id=${studentId}`),
      api<Assignment[]>(`/api/v1/students/${studentId}/topic-learning-assignments`),
    ]).then(async ([focusResult, riskResult, assignmentResult]) => {
      const studentRisk = riskResult.items.find((item) => item.id === studentId);
      if (!studentRisk) throw new Error('Student is not in your assigned classes.');
      const academicPeriod = riskResult.academic_period;
      const academicPeriodQuery = academicPeriod
        ? `?academic_period=${encodeURIComponent(academicPeriod)}`
        : '';
      const academicResult = await api<AcademicRecords>(
        `/api/v1/students/${studentId}/academic-records${academicPeriodQuery}`,
      );
      const eligibleSubjects = eligibleSubjectsForStudent(focusResult, academicResult.records, studentId);
      const resourceEntries = await Promise.all(eligibleSubjects.map(async (subject) => {
        const [modules, topics] = await Promise.all([
          api<Module[]>(`/api/v1/subjects/${subject.id}/modules`),
          api<Topic[]>(`/api/v1/subjects/${subject.id}/topics`),
        ]);
        return [subject.id, { modules, topics }] as const;
      }));
      if (!active) return;
      setFocus(focusResult);
      setRiskStudent({ ...studentRisk, academic_period: academicResult.academic_period });
      setAcademicRecords(academicResult.records);
      setAssignments(assignmentResult);
      setSubjectResources(Object.fromEntries(resourceEntries));
    }).catch((requestError: Error) => {
      if (active) setError(requestError.message);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [studentId]);

  const eligibleSubjects = useMemo(
    () => eligibleSubjectsForStudent(focus, academicRecords, studentId),
    [focus, academicRecords, studentId],
  );
  async function verifyFa1Maximum(record: AcademicRecord) {
    const sourceReference = sourceReferences[record.record_id]?.trim();
    if (!sourceReference) {
      setError('Enter the official source reference reviewed for this FA-1 maximum.');
      return;
    }
    setReviewingRecordId(record.record_id);
    setError('');
    try {
      const period = riskStudent?.academic_period ?? record.academic_period;
      const updatedRecord = await api<AcademicRecord>(
        `/api/v1/academic-register/${studentId}/${encodeURIComponent(record.code)}/fa1-maximum-verification?academic_period=${encodeURIComponent(period)}`,
        {
          method: 'POST',
          body: JSON.stringify({ source_reference: sourceReference }),
        },
      );
      const query = new URLSearchParams({ student_id: String(studentId), first_internal_only: 'true' });
      const updatedFocus = await api<FocusResponse>(`/api/v1/teacher/subject-focus?${query}`);
      setAcademicRecords((current) => current.map((item) => (
        item.record_id === updatedRecord.record_id ? updatedRecord : item
      )));
      setFocus(updatedFocus);
      setSourceReferences((current) => {
        const next = { ...current };
        delete next[record.record_id];
        return next;
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to verify FA-1 maximum evidence.');
    } finally {
      setReviewingRecordId(null);
    }
  }
  const focusStudent = focus?.subjects
    .flatMap((subject) => subject.students)
    .find((student) => student.id === studentId);
  const officialFailSubjects = focus?.subjects.filter((subject) => (
    subject.students.find((student) => student.id === studentId)?.official_failure === true
  )) ?? [];
  const evidenceRows = academicRecords.map((record) => {
    const focusedSubject = focus?.subjects.find((subject) => subject.id === record.subject_id);
    const student = focusedSubject?.students.find((item) => item.id === studentId);
    const assessment = student?.history.find(
      (item) => item.evidence_id === `academic-record:${record.record_id}:fa1`,
    ) ?? null;
    const eligible = eligibleSubjects.some((subject) => subject.id === record.subject_id);
    return { record, focusedSubject, student, assessment, state: evidenceState(record, assessment, eligible) };
  });
  const profileStudent = riskStudent ?? focusStudent;

  return <div className="fade-up space-y-5">
    <Link href="/teacher/recovery" className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white"><ArrowLeft size={14} />Recovery directory</Link>
    {error && <ErrorNotice message={error} />}
    {focus?.policy && !focus.policy.active_for_classification && <div role="alert" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-xs text-amber-100">The configured normalized-mark policy is inactive. No subject is treated as FA-1 eligible until verified 25-mark policy evidence is available.</div>}
    {profileStudent && <PageHeading
      eyebrow={`${profileStudent.student_id}${profileStudent.section ? ` · Class ${profileStudent.section}` : ''}${riskStudent?.course_name ? ` · ${riskStudent.course_name}` : ''}`}
      title={profileStudent.full_name}
      description="Strict FA-1 eligibility is based only on verified evidence normalized to 25 and strictly below 12. Official FAIL remains a separate support signal."
      action={<Link href={`/teacher/recovery/roadmaps?student_id=${studentId}`} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25"><CalendarCheck2 size={14} />Open roadmap & schedule</Link>}
    />}
    {loading && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading verified FA-1 evidence and persisted recovery progress…</div>}
    {!loading && !error && riskStudent && focus && <section aria-label="Recovery evidence summary" className="grid gap-3 sm:grid-cols-3">
      <Metric label="Verified eligible subjects" value={eligibleSubjects.length} detail="Strictly below 12/25 with verified evidence" color={eligibleSubjects.length ? 'rose' : 'amber'} />
      <Metric label="Subject records reviewed" value={evidenceRows.length} detail="From the selected academic period" color="cyan" />
      <Metric label="Academic period" value={riskStudent.academic_period ?? 'Not specified'} detail="Used for this evidence view" color="amber" />
    </section>}
    {!loading && !error && profileStudent && <section aria-label="Student risk indicators" className="surface rounded-xl p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="eyebrow">Academic risk indicators</p><h2 className="mt-1 text-sm font-semibold text-white">Persisted class evidence</h2></div>
        <RiskBadge level={riskStudent?.risk_category ?? 'INSUFFICIENT_EVIDENCE'} />
      </div>
      <p className="mt-1 text-[10px] text-slate-500">Risk category is the recorded academic outcome, not a new prediction.</p>
      {riskStudent?.risk_outcome && <p className="mt-2 text-[10px] text-slate-400">Recorded {riskStudent.risk_outcome.risk_level} outcome · {riskStudent.risk_outcome.outcome_source} · {riskStudent.risk_outcome.academic_period} · {dateLabel(riskStudent.risk_outcome.observed_at)}</p>}
      {riskStudent?.indicators.length
        ? <ul className="mt-3 grid gap-2 sm:grid-cols-2">{riskStudent.indicators.map((indicator) => <li key={indicator} className="rounded-lg border border-white/[.07] p-3 text-[10px] leading-5 text-slate-300">{indicator}</li>)}</ul>
        : <p className="mt-3 text-xs text-slate-400">No independent risk indicators were returned for this student.</p>}
      {officialFailSubjects.length > 0 && <div className="mt-3 rounded-lg border border-rose-200/15 bg-rose-200/[.035] p-3 text-[10px] leading-5 text-rose-100"><strong>Separate official FAIL support:</strong> {officialFailSubjects.map((subject) => subject.name).join(', ')}. These subjects are not added to the strict FA-1 eligibility list unless their verified score also meets the threshold.</div>}
    </section>}
    {!loading && !error && focus && !eligibleSubjects.length && <div className="surface rounded-xl p-5"><Empty title="No strictly eligible FA-1 subjects" detail="A subject appears below only when the first-internal evidence is dated and verified, its maximum is verified, and its normalized score is strictly below 12/25. Official FAIL is reported separately above." /></div>}
    {!loading && !error && riskStudent && <section aria-label="Subject-wise FA-1 Evidence" className="surface rounded-xl p-4 sm:p-5">
      <details>
        <summary className="cursor-pointer list-none">
          <span className="eyebrow">Evidence provenance · {evidenceRows.length} subject record{evidenceRows.length === 1 ? '' : 's'}</span>
          <span className="mt-1 block text-sm font-semibold text-white">Subject-wise FA-1 Evidence</span>
          <span className="mt-1 block text-[10px] leading-5 text-slate-500">Expand to review source records, dates, and verification. Rows come from Internal Marks for the selected period; a raw mark or /25 display is not proof of a verified scale.</span>
        </summary>
      {!evidenceRows.length
        ? <p className="mt-3 text-xs text-slate-400">No subject academic records were returned for this academic period.</p>
        : <ul className="mt-3 space-y-2">{evidenceRows.map(({ record, assessment, state }) => <li key={record.record_id} className="rounded-lg border border-white/[.07] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[11px] font-medium text-slate-200">{record.code} · {record.name} · FA-1 <span className="text-slate-500">· subject {record.subject_id} · record {record.record_id}</span></p>
          <span className={`rounded-full border px-2 py-1 text-[9px] ${state.label === 'ELIGIBLE FOR RECOVERY' ? 'border-emerald-200/20 text-emerald-100' : 'border-amber-200/20 text-amber-100'}`}>{state.label}</span>
        </div>
        <dl className="mt-2 grid gap-2 text-[10px] sm:grid-cols-2">
          <div><dt className="text-slate-500">Raw FA-1 mark</dt><dd className="mt-1 text-slate-300">{record.fa1 ?? 'Not recorded'}</dd></div>
          <div><dt className="text-slate-500">Verified maximum marks</dt><dd className="mt-1 text-slate-300">{record.fa1_maximum_verified ? record.fa1_maximum_marks : 'Not recorded/verified in the Internal Marks record'}</dd></div>
          <div><dt className="text-slate-500">Verified normalized mark</dt><dd className="mt-1 text-slate-300">{assessment?.evidence_status === 'VERIFIED' && record.fa1_maximum_verified && assessment.maximum_marks === record.fa1_maximum_marks && assessment.normalized_score !== null ? `${assessment.normalized_score.toFixed(2)} / 25` : 'Not available — score scale unverified'}</dd></div>
          <div><dt className="text-slate-500">Assessment date</dt><dd className="mt-1 text-slate-300">{record.fa1_observed_at ? dateLabel(record.fa1_observed_at) : 'Date not recorded'}{assessment?.recorded_at && assessment.recorded_at !== record.fa1_observed_at ? ` · assessment evidence recorded ${dateLabel(assessment.recorded_at)}` : ''}</dd></div>
          <div><dt className="text-slate-500">Academic period</dt><dd className="mt-1 text-slate-300">Subject record: {record.academic_period} · Assessment evidence: {assessment?.academic_period ?? 'not provided'}</dd></div>
          <div><dt className="text-slate-500">Date provenance</dt><dd className="mt-1 text-slate-300">{record.temporal_evidence_verified ? 'Verified' : record.fa1_observed_at ? 'Date present but unverified' : 'Date/provenance missing'}</dd></div>
          <div><dt className="text-slate-500">Assessment verification</dt><dd className="mt-1 text-slate-300">{assessment ? `${assessment.evidence_status.replaceAll('_', ' ')} · ${assessment.source}` : 'No verified assessment evidence returned'}</dd></div>
          <div className="sm:col-span-2"><dt className="text-slate-500">Source / import reference</dt><dd className="mt-1 break-words text-slate-300">{typeof record.import_batch_id === 'number' ? `Import #${record.import_batch_id}${record.source_filename ? ` · ${record.source_filename}` : ''}` : 'Import reference unavailable'}</dd></div>
          <div className="sm:col-span-2"><dt className="text-slate-500">FA-1 maximum source review</dt><dd className="mt-1 break-words text-slate-300">{record.fa1_maximum_source_reference ?? (record.fa1_maximum_verified ? 'Verified; source reference unavailable' : 'Not reviewed')}</dd></div>
        </dl>
        {!record.fa1_maximum_verified && record.fa1_maximum_marks !== null && record.fa1_observed_at && record.temporal_evidence_verified && record.import_batch_id && record.source_filename && <form
          className="mt-3 flex flex-col gap-2 rounded-lg border border-white/[.07] p-3 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            void verifyFa1Maximum(record);
          }}
        >
          <label className="flex-1 text-[10px] text-slate-400">
            Official source reference reviewed
            <input
              value={sourceReferences[record.record_id] ?? ''}
              onChange={(event) => setSourceReferences((current) => ({ ...current, [record.record_id]: event.target.value }))}
              placeholder="File/page/cell or assessment register reference"
              className="form-input mt-1"
              minLength={5}
              maxLength={500}
              required
            />
          </label>
          <button type="submit" disabled={reviewingRecordId === record.record_id} className="mt-auto min-h-10 rounded-lg border border-emerald-200/20 px-3 text-[10px] text-emerald-100 disabled:opacity-50">
            {reviewingRecordId === record.record_id ? 'Saving review…' : 'Verify maximum from source'}
          </button>
        </form>}
        <p className={`mt-2 text-[10px] leading-4 ${state.label === 'ELIGIBLE FOR RECOVERY' ? 'text-emerald-100' : 'text-amber-100'}`}>{state.reason}</p>
      </li>)}</ul>}
      </details>
      {evidenceRows.length > 0 && <div className="mt-4 border-t border-white/[.07] pt-4">
        <h3 className="text-xs font-semibold text-slate-200">Teacher module review</h3>
        <p className="mt-1 text-[10px] leading-5 text-slate-500">Available for subjects in your assigned teaching scope. Opening a workspace does not change evidence or recovery eligibility.</p>
        <ul className="mt-2 grid gap-2 sm:grid-cols-2">{evidenceRows.map(({ record, focusedSubject, state }) =>
          <li key={record.record_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[.07] p-3">
            <div><p className="text-[10px] font-medium text-slate-200">{record.code} · {record.name}</p><p className="mt-1 text-[9px] text-slate-500">{state.label}</p></div>
            {focusedSubject
              ? <Link
                href={`/teacher/recovery/subjects/${record.subject_id}/students/${studentId}`}
                className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] font-medium text-slate-200 transition hover:border-rose-300/25 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
              >
                Review {record.name} modules <ArrowRight size={12} />
              </Link>
              : <p className="text-[9px] text-slate-500">Workspace unavailable — subject is outside your assigned teaching scope.</p>}
          </li>)}</ul>
      </div>}
    </section>}
    {eligibleSubjects.map((subject) => {
      const evidence = subject.students.find((student) => student.id === studentId);
      const progress = subjectResources[subject.id];
      const approvedTopics = progress?.topics.filter((topic) => topic.approval_status === 'APPROVED') ?? [];
      const awaitingTopics = progress?.topics.filter((topic) => ['DRAFT', 'DEFERRED'].includes(topic.approval_status)) ?? [];
      const rejectedTopics = progress?.topics.filter((topic) => topic.approval_status === 'REJECTED') ?? [];
      const subjectAssignments = assignments.filter((assignment) => assignment.subject_id === subject.id);
      const assignmentByTopic = new Map(subjectAssignments.map((assignment) => [assignment.approved_topic_id, assignment]));
      const completedTopicIds = new Set(subjectAssignments.filter((assignment) => assignment.status === 'MASTERED').map((assignment) => assignment.approved_topic_id));
      const nextTopic = approvedTopics.find((topic) => !completedTopicIds.has(topic.id));
      const nextAssignment = nextTopic ? assignmentByTopic.get(nextTopic.id) : undefined;
      const modules = new Map((progress?.modules ?? []).map((module) => [module.module_number, module]));

      return <article key={subject.id} aria-label={`${subject.name} recovery progress`} className="surface space-y-4 rounded-xl p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-lg border border-rose-200/15 bg-rose-200/[.05] text-rose-100"><BookOpenCheck size={18} /></span><div><p className="eyebrow">{subject.code} · FA-1 eligible</p><h2 className="mt-1 text-base font-semibold text-white">{subject.name}</h2></div></div>
          <div className="flex flex-wrap gap-2">
            <Link href={`/teacher/recovery/subjects/${subject.id}/students/${studentId}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white hover:bg-rose-500">Upload Modules <ArrowRight size={12} /></Link>
            <Link href={`/teacher/recovery/roadmaps?student_id=${studentId}&subject_id=${subject.id}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] font-semibold text-slate-200 hover:border-rose-300/25">Individual Subject Roadmap <CalendarCheck2 size={12} /></Link>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-white/[.07] p-3"><p className="text-[9px] text-slate-500">Evidence status</p><p className="mt-1 text-xs text-emerald-100">VERIFIED · ELIGIBLE</p><p className="mt-1 text-[10px] text-slate-400">{evidence?.latest_assessment?.assessment ?? 'FA-1'} · {evidence?.latest_assessment?.score ?? '—'} / {evidence?.latest_assessment?.maximum_marks ?? '—'} marks</p></div>
          <div className="rounded-lg border border-white/[.07] p-3"><p className="text-[9px] text-slate-500">Verified normalized score</p><p className="mt-1 text-xs font-semibold text-rose-100">{evidence?.latest_assessment?.normalized_score?.toFixed(2) ?? '—'} / 25</p><p className="mt-1 text-[10px] text-slate-400">{dateLabel(evidence?.latest_assessment?.recorded_at ?? null)}</p></div>
          <div className="rounded-lg border border-white/[.07] p-3"><p className="text-[9px] text-slate-500">Module topics mastered</p><p className="mt-1 text-xs text-white">{completedTopicIds.size} of {approvedTopics.length} approved</p><p className="mt-1 text-[10px] text-slate-400">{subjectAssignments.length} persisted topic assignment{subjectAssignments.length === 1 ? '' : 's'}</p></div>
          <div className="rounded-lg border border-white/[.07] p-3"><p className="text-[9px] text-slate-500">Current learning activity</p><p className="mt-1 text-xs text-white">{nextTopic?.title ?? (approvedTopics.length ? 'All approved topics mastered' : 'Awaiting approved topics')}</p><p className="mt-1 text-[10px] text-slate-400">{nextAssignment ? nextAssignment.status.replaceAll('_', ' ').toLowerCase() : nextTopic ? 'Not assigned yet' : 'No current topic'}</p></div>
        </div>
        {progress && <div>
          <h3 className="text-xs font-semibold text-slate-200">Five-module learning highway</h3>
          <ol className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {Array.from({ length: 5 }, (_, index) => {
              const moduleNumber = index + 1;
              const subjectModule = modules.get(moduleNumber);
              const moduleTopics = progress.topics.filter((topic) => topic.module_id === subjectModule?.id);
              const approvedInModule = moduleTopics.filter((topic) => topic.approval_status === 'APPROVED');
              const masteredInModule = approvedInModule.filter((topic) => completedTopicIds.has(topic.id)).length;
              const awaitingInModule = moduleTopics.filter((topic) => ['DRAFT', 'DEFERRED'].includes(topic.approval_status)).length;
              const rejectedInModule = moduleTopics.filter((topic) => topic.approval_status === 'REJECTED').length;
              return <li key={moduleNumber} className={`rounded-lg border p-3 ${moduleNumber === 3 || moduleNumber === 4 ? 'border-rose-200/15 bg-rose-200/[.025]' : 'border-white/[.07]'}`}>
                <p className="text-[9px] font-semibold uppercase tracking-[.08em] text-slate-400">Module {moduleNumber}</p>
                <p className="mt-1 truncate text-[10px] font-medium text-white">{subjectModule?.title ?? 'Not set up'}</p>
                <p className="mt-2 text-[9px] leading-4 text-slate-500">{subjectModule?.resource_count ?? 0} uploaded sources · {masteredInModule}/{approvedInModule.length} topics mastered</p>
                {awaitingInModule > 0 && <p className="mt-1 flex items-center gap-1 text-[9px] text-amber-100"><CircleAlert size={10} />{awaitingInModule} topic{awaitingInModule === 1 ? '' : 's'} awaiting teacher approval</p>}
                {rejectedInModule > 0 && <p className="mt-1 text-[9px] text-rose-200">{rejectedInModule} topic{rejectedInModule === 1 ? '' : 's'} returned for revision</p>}
              </li>;
            })}
          </ol>
        </div>}
        {progress && <div className="rounded-lg border border-white/[.07] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold text-slate-200">Persisted topic and assessment status</h3><span className="text-[10px] text-slate-500">{approvedTopics.length} approved · {awaitingTopics.length} awaiting review · {rejectedTopics.length} returned</span></div>
          {approvedTopics.length
            ? <ol className="mt-2 space-y-2">{approvedTopics.map((topic, index) => {
              const assignment = assignmentByTopic.get(topic.id);
              const mastered = assignment?.status === 'MASTERED';
              return <li key={topic.id} className="flex flex-wrap items-start justify-between gap-2 border-t border-white/[.05] pt-2 first:border-0">
                <div><p className="text-[10px] text-slate-200">{index + 1}. {topic.title}</p><p className="mt-1 text-[9px] text-slate-500">{assignment ? `Assignment: ${assignment.status.replaceAll('_', ' ').toLowerCase()}` : 'Approved; not assigned'}{assignment?.assessment ? ` · assessment ${assignment.assessment.status.toLowerCase()} (${assignment.assessment.question_count} questions)` : ''}{assignment?.latest_attempt ? ` · latest attempt ${assignment.latest_attempt.status.toLowerCase()}${assignment.latest_attempt.percentage === null ? '' : `, ${assignment.latest_attempt.percentage}%`}` : ''}{assignment?.latest_attempt?.review_pending ? ' · teacher review pending' : ''}</p></div>
                <span className={`rounded-full border px-2 py-1 text-[9px] ${mastered ? 'border-emerald-200/20 text-emerald-100' : 'border-white/10 text-slate-400'}`}>{mastered ? 'Mastered' : assignment?.status.replaceAll('_', ' ') ?? 'Remaining'}</span>
              </li>;
            })}</ol>
            : <p className="mt-2 text-[10px] text-slate-500">No teacher-approved topics have been persisted for this subject yet.</p>}
          {(awaitingTopics.length > 0 || rejectedTopics.length > 0) && <ul className="mt-2 space-y-2 border-t border-white/[.05] pt-2">{[...awaitingTopics, ...rejectedTopics].map((topic) => <li key={topic.id} className="flex flex-wrap justify-between gap-2 text-[10px]"><span className="text-slate-300">{topic.title}</span><span className={topic.approval_status === 'REJECTED' ? 'text-rose-200' : 'text-amber-100'}>{topic.approval_status === 'REJECTED' ? 'Returned for revision' : 'Awaiting teacher approval'}</span></li>)}</ul>}
        </div>}
        {!progress && <p role="status" className="text-xs text-slate-400">Loading persisted module and topic status…</p>}
        <p className="text-[10px] leading-5 text-slate-500"><ShieldCheck size={12} className="mr-1 inline" />New Module 4 assignments are backend-gated until every approved Module 3 topic across all strictly eligible subjects is mastered. FA-2 dates are teacher-confirmed in the roadmap workflow; no date is inferred here.</p>
      </article>;
    })}
  </div>;
}

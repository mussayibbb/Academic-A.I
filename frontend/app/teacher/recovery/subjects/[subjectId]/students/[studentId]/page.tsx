'use client';

import { ArrowLeft, ClipboardCheck } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import SubjectModuleRecoveryPanel from '@/components/SubjectModuleRecoveryPanel';
import { ErrorNotice, PageHeading } from '@/components/ui';
import { api } from '@/lib/api';

type FocusEvidence = {
  evidence_id: string;
  assessment: string;
  score: number | null;
  maximum_marks: number | null;
  maximum_marks_verified: boolean;
  normalized_score: number | null;
  recorded_at: string | null;
  official_result: string | null;
  source: string;
  academic_period: string | null;
  evidence_status: string;
  reason: string | null;
};
type FocusStudent = {
  id: number;
  student_id: string;
  full_name: string;
  section: string | null;
  attendance_rate: number | null;
  attendance_observed_at: string | null;
  attendance_academic_period: string | null;
  status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
  reason: string;
  latest_assessment: FocusEvidence | null;
  latest_assessments: FocusEvidence[];
  official_failure: boolean | null;
  support_triggers: ('LOW_SCORE' | 'OFFICIAL_FAIL')[];
  history: FocusEvidence[];
};
type SubjectFocus = {
  id: number;
  code: string;
  name: string;
  students: FocusStudent[];
};
type FocusResponse = {
  policy: {
    version: number;
    threshold_score: number;
    threshold_maximum: number;
    strictly_below: boolean;
    active_for_classification: boolean;
  } | null;
  subjects: SubjectFocus[];
};

function displayedDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Date unavailable';
}

export default function TeacherSubjectStudentWorkspacePage() {
  const params = useParams<{ subjectId: string; studentId: string }>();
  const subjectId = Number(params.subjectId);
  const studentId = Number(params.studentId);
  const [data, setData] = useState<FocusResponse | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (![subjectId, studentId].every((value) => Number.isInteger(value) && value > 0)) {
      setError('Invalid subject or student identifier.');
      return;
    }
    const query = new URLSearchParams({
      subject_id: String(subjectId),
      student_id: String(studentId),
      first_internal_only: 'true',
    });
    api<FocusResponse>(`/api/v1/teacher/subject-focus?${query}`)
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message));
  }, [studentId, subjectId]);

  const subject = data?.subjects[0];
  const student = subject?.students[0];
  const reason = student?.reason;
  const blueprintUrl = `/teacher/recovery/roadmaps?student_id=${studentId}&subject_id=${subjectId}`;

  return <div className="fade-up space-y-5">
    <Link href={`/teacher/recovery/subjects/${subjectId}`} className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white"><ArrowLeft size={14} />Subject student list</Link>
    {error && <ErrorNotice message={error} />}
    {data?.policy && !data.policy.active_for_classification && <div role="alert" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-xs text-amber-100">Stored subject-support policy uses a non-current normalized scale. Marks-based classification remains insufficient until the reviewed 12/25 policy migration is applied.</div>}
    {subject && student && <PageHeading
      eyebrow={`${subject.code} · teacher workspace`}
      title={student.full_name}
      description={`${student.student_id}${student.section ? ` · Class ${student.section}` : ''} · ${subject.name}`}
      action={<Link href={blueprintUrl} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white hover:bg-rose-500"><ClipboardCheck size={14} />Open roadmap builder & monitoring</Link>}
    />}
    {!data && !error && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading this student’s persisted subject evidence…</div>}
    {subject && student && <section className="surface rounded-xl p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="eyebrow">First internal assessment · versioned rule v{data.policy?.version ?? 'unavailable'}</p><h2 className="mt-1 text-sm font-semibold text-white">Subject support evidence</h2></div>
        <span className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${student.status === 'LOW' ? 'border-rose-200/20 bg-rose-200/[.05] text-rose-100' : student.status === 'INSUFFICIENT_EVIDENCE' ? 'border-amber-200/20 text-amber-100' : 'border-white/10 text-slate-200'}`}>{student.status === 'LOW' ? 'Below 12/25' : student.status === 'NOT_LOW' ? 'Not LOW by marks' : 'Insufficient evidence'}</span>
      </div>
      {student.latest_assessment
        ? <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Assessment</dt><dd className="mt-1 text-xs text-white">{student.latest_assessment.assessment}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Recorded marks</dt><dd className="mt-1 text-xs text-white">{student.latest_assessment.score ?? '—'} / {student.latest_assessment.maximum_marks ?? '—'}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Normalized support score</dt><dd className="mt-1 text-xs text-white">{student.latest_assessment.normalized_score?.toFixed(2) ?? '—'} / {data.policy?.threshold_maximum ?? '—'}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Recorded</dt><dd className="mt-1 text-xs text-white">{displayedDate(student.latest_assessment.recorded_at)}</dd></div>
        </dl>
        : <p className="mt-4 rounded-lg border border-amber-200/10 bg-amber-200/[.025] p-3 text-xs text-amber-100">No usable latest assessment is available. {student.reason}</p>}
      <p className="mt-3 text-xs leading-5 text-slate-300">{student.reason}</p>
      {student.attendance_rate !== null && student.attendance_rate !== undefined
        ? <p className="mt-2 text-[10px] text-slate-400">Latest persisted attendance: {student.attendance_rate}%{student.attendance_observed_at ? ` · ${displayedDate(student.attendance_observed_at)}` : ''}{student.attendance_academic_period ? ` · ${student.attendance_academic_period}` : ''}</p>
        : <p className="mt-2 text-[10px] text-amber-100">No dated attendance evidence is persisted for this subject.</p>}
      <p className="mt-2 text-[10px] text-slate-500">A marks-based LOW indicator is separate from official results. Official FAIL independently triggers subject support.</p>
      {student.support_triggers.includes('OFFICIAL_FAIL') && <p className="mt-2 text-[10px] font-medium text-rose-100">Support trigger: explicitly recorded official FAIL.</p>}
      <div className="mt-3 rounded-lg border border-white/[.07] p-3 text-xs"><span className="text-slate-500">Official result, independent of support threshold: </span>{student.official_failure === true ? <strong className="text-rose-100">FAIL explicitly recorded</strong> : student.latest_assessment?.official_result ? <strong className="text-slate-200">{student.latest_assessment.official_result} explicitly recorded</strong> : <span className="text-slate-400">not recorded</span>}</div>

      <details className="mt-4 rounded-lg border border-white/[.07] p-3">
        <summary className="cursor-pointer text-xs font-medium text-slate-200">Assessment history and trend ({student.history.length} recorded)</summary>
        {student.history.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[620px] text-left text-[10px]"><thead><tr className="border-b border-white/[.07] text-slate-500"><th className="py-2">Date</th><th>Assessment</th><th>Score</th><th>Normalized</th><th>Official result</th><th>Source / period</th></tr></thead><tbody>{student.history.map((item) => <tr key={item.evidence_id} className="border-b border-white/[.04] text-slate-300 last:border-0"><td className="py-2">{displayedDate(item.recorded_at)}</td><td>{item.assessment}</td><td>{item.score ?? '—'} / {item.maximum_marks ?? '—'}{!item.maximum_marks_verified && <span className="ml-1 text-amber-100">· max unverified</span>}</td><td>{item.normalized_score?.toFixed(2) ?? 'Insufficient evidence'}</td><td>{item.official_result ?? 'Not recorded'}</td><td>{item.source}{item.academic_period ? ` · ${item.academic_period}` : ''}</td></tr>)}</tbody></table></div> : <p className="mt-3 text-xs text-slate-500">No assessment history is persisted for this subject.</p>}
      </details>
    </section>}
    {subject && student && <SubjectModuleRecoveryPanel studentId={student.id} initialSubjectId={subject.id} subjectFocusReason={reason} />}
  </div>;
}

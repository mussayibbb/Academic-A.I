'use client';

import { ArrowLeft, ClipboardCheck } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import StudentLearningRecoveryPanel from '@/components/StudentLearningRecoveryPanel';
import { Empty, ErrorNotice, PageHeading } from '@/components/ui';
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
type FocusSubject = {
  id: number;
  code: string;
  name: string;
  status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
  reason: string;
  latest_assessment: FocusEvidence | null;
  latest_assessments: FocusEvidence[];
  official_failure: boolean | null;
  support_triggers: ('LOW_SCORE' | 'OFFICIAL_FAIL')[];
  history: FocusEvidence[];
};
type FocusResponse = {
  policy: {
    version: number;
    threshold_score: number;
    threshold_maximum: number;
    strictly_below: boolean;
    active_for_classification: boolean;
  } | null;
  subjects: FocusSubject[];
};

function displayedDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Date unavailable';
}

export default function StudentSubjectWorkspacePage() {
  const params = useParams<{ subjectId: string }>();
  const subjectId = Number(params.subjectId);
  const [data, setData] = useState<FocusResponse | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api<FocusResponse>('/api/v1/students/me/subject-focus?first_internal_only=true')
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message));
  }, []);

  const subject = data?.subjects.find((item) => item.id === subjectId);

  return <div className="fade-up space-y-5">
    <Link href="/student/subjects" className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white"><ArrowLeft size={14} />My subjects</Link>
    {error && <ErrorNotice message={error} />}
    {data?.policy && !data.policy.active_for_classification && <div role="alert" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-xs text-amber-100">Stored subject-support policy uses a non-current normalized scale. Marks-based classification remains insufficient until the reviewed 12/25 policy migration is applied.</div>}
    {subject && <PageHeading
      eyebrow={`${subject.code} · your subject workspace`}
      title={subject.name}
      description="Your first-internal support status, full recorded assessment history, and teacher-published subject learning."
      action={<Link href={`/student/recovery?subject=${encodeURIComponent(subject.name)}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] text-slate-200 hover:border-rose-300/25"><ClipboardCheck size={13} />Roadmap tasks</Link>}
    />}
    {!data && !error && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading your persisted subject records…</div>}
    {data && !subject && <div className="surface rounded-xl p-5"><Empty title="Subject workspace unavailable" detail="This subject is not linked to your records or published learning assignments." /></div>}
    {subject && <section className="surface rounded-xl p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="eyebrow">First-internal academic support · rule v{data?.policy?.version ?? 'unavailable'}</p><h2 className="mt-1 text-sm font-semibold text-white">Your first internal assessment</h2></div>
        <span className={`rounded-full border px-3 py-1.5 text-[10px] font-semibold ${subject.status === 'LOW' ? 'border-rose-200/20 bg-rose-200/[.05] text-rose-100' : subject.status === 'INSUFFICIENT_EVIDENCE' ? 'border-amber-200/20 text-amber-100' : 'border-white/10 text-slate-200'}`}>{subject.status === 'LOW' ? 'Below 12/25' : subject.status === 'NOT_LOW' ? 'Not LOW by marks' : 'Insufficient evidence'}</span>
      </div>
      {subject.latest_assessment
        ? <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Assessment</dt><dd className="mt-1 text-xs text-white">{subject.latest_assessment.assessment}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Recorded marks</dt><dd className="mt-1 text-xs text-white">{subject.latest_assessment.score ?? '—'} / {subject.latest_assessment.maximum_marks ?? '—'}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Normalized score</dt><dd className="mt-1 text-xs text-white">{subject.latest_assessment.normalized_score?.toFixed(2) ?? '—'} / {data?.policy?.threshold_maximum ?? '—'}</dd></div>
          <div className="rounded-lg border border-white/[.07] p-3"><dt className="text-[9px] text-slate-500">Recorded</dt><dd className="mt-1 text-xs text-white">{displayedDate(subject.latest_assessment.recorded_at)}</dd></div>
        </dl>
        : <p className="mt-4 rounded-lg border border-amber-200/10 bg-amber-200/[.025] p-3 text-xs text-amber-100">{subject.reason}</p>}
      <p className="mt-3 text-xs leading-5 text-slate-300">{subject.reason}</p>
      <p className="mt-2 text-[10px] text-slate-500">Marks-based support is not an official grade. An explicitly recorded official FAIL independently triggers support.</p>
      {subject.support_triggers.includes('OFFICIAL_FAIL') && <p className="mt-2 text-[10px] font-medium text-rose-100">Support trigger: explicitly recorded official FAIL.</p>}
      <div className="mt-3 rounded-lg border border-white/[.07] p-3 text-xs"><span className="text-slate-500">Explicit official result: </span>{subject.official_failure === true ? <strong className="text-rose-100">FAIL</strong> : subject.latest_assessment?.official_result ? <strong className="text-slate-200">{subject.latest_assessment.official_result}</strong> : <span className="text-slate-400">not recorded</span>}</div>
      <details className="mt-4 rounded-lg border border-white/[.07] p-3">
        <summary className="cursor-pointer text-xs font-medium text-slate-200">Assessment history ({subject.history.length})</summary>
        {subject.history.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[600px] text-left text-[10px]"><thead><tr className="border-b border-white/[.07] text-slate-500"><th className="py-2">Date</th><th>Assessment</th><th>Marks</th><th>Normalized</th><th>Official result</th><th>Source / period</th></tr></thead><tbody>{subject.history.map((item) => <tr key={item.evidence_id} className="border-b border-white/[.04] text-slate-300 last:border-0"><td className="py-2">{displayedDate(item.recorded_at)}</td><td>{item.assessment}</td><td>{item.score ?? '—'} / {item.maximum_marks ?? '—'}{!item.maximum_marks_verified && <span className="ml-1 text-amber-100">· max unverified</span>}</td><td>{item.normalized_score?.toFixed(2) ?? 'Insufficient evidence'}</td><td>{item.official_result ?? 'Not recorded'}</td><td>{item.source}{item.academic_period ? ` · ${item.academic_period}` : ''}</td></tr>)}</tbody></table></div> : <p className="mt-3 text-xs text-slate-500">No assessment history is available.</p>}
      </details>
    </section>}
    {subject && <StudentLearningRecoveryPanel subjectId={subject.id} />}
  </div>;
}

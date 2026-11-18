'use client';

import { ArrowLeft, ArrowRight, Search } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import SubjectModuleRecoveryPanel from '@/components/SubjectModuleRecoveryPanel';
import { Empty, ErrorNotice, PageHeading } from '@/components/ui';
import { api } from '@/lib/api';

type FocusEvidence = {
  score: number | null;
  maximum_marks: number | null;
  normalized_score: number | null;
  assessment: string;
  recorded_at: string | null;
  official_result: string | null;
};
type FocusStudent = {
  id: number;
  student_id: string;
  full_name: string;
  section: string | null;
  status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
  reason: string;
  latest_assessment: FocusEvidence | null;
  official_failure: boolean | null;
};
type SubjectFocus = {
  id: number;
  code: string;
  name: string;
  students: FocusStudent[];
  counts: Record<'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE', number>;
};
type FocusResponse = {
  policy: {
    version: number;
    threshold_score: number;
    threshold_maximum: number;
    active_for_classification: boolean;
  } | null;
  subjects: SubjectFocus[];
};

function statusLabel(status: FocusStudent['status']): string {
  if (status === 'LOW') return 'Below support threshold';
  if (status === 'NOT_LOW') return 'Not below threshold';
  return 'Insufficient evidence';
}

export default function TeacherSubjectRosterPage() {
  const params = useParams<{ subjectId: string }>();
  const subjectId = Number(params.subjectId);
  const [data, setData] = useState<FocusResponse | null>(null);
  const [requestedStudentId, setRequestedStudentId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get('student_id');
    if (value && Number.isInteger(Number(value)) && Number(value) > 0) setRequestedStudentId(Number(value));
  }, []);

  useEffect(() => {
    if (!Number.isInteger(subjectId) || subjectId <= 0) {
      setError('Invalid subject identifier.');
      return;
    }
    const query = new URLSearchParams({ subject_id: String(subjectId), first_internal_only: 'true' });
    if (requestedStudentId) query.set('student_id', String(requestedStudentId));
    api<FocusResponse>(`/api/v1/teacher/subject-focus?${query}`)
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message));
  }, [requestedStudentId, subjectId]);

  const subject = data?.subjects[0];
  const filteredStudents = useMemo(() => (subject?.students ?? []).filter((student) => {
    const textMatch = `${student.full_name} ${student.student_id} ${student.section ?? ''}`.toLowerCase().includes(search.trim().toLowerCase());
    return textMatch && (!statusFilter || student.status === statusFilter);
  }), [search, statusFilter, subject]);

  return <div className="fade-up space-y-5">
    <Link href={requestedStudentId ? `/teacher/recovery/students/${requestedStudentId}` : '/teacher/recovery'} className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white"><ArrowLeft size={14} />{requestedStudentId ? 'Student recovery profile' : 'Subject overview'}</Link>
    {error && <ErrorNotice message={error} />}
    {subject && <PageHeading
      eyebrow={`${subject.code} · evidence-ranked students`}
      title={subject.name}
      description="First-internal marks determine the support indicator when verified; missing or ambiguous evidence stays separate. Explicit official failures are shown independently."
      action={subject.students.length > 0 ? <Link prefetch={false} href={`/teacher/recovery/subjects/${subject.id}/students/${requestedStudentId ?? subject.students[0].id}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] text-slate-200 hover:border-rose-300/25"><ArrowRight size={13} />Open first student workspace</Link> : undefined}
    />}
    {data?.policy?.active_for_classification
      ? <p className="rounded-lg border border-white/[.07] px-4 py-3 text-[10px] text-slate-400">Rule v{data.policy.version}: LOW means the first-internal normalized score is strictly below {data.policy.threshold_score}/{data.policy.threshold_maximum}. This support flag is not the official pass/fail result.</p>
      : data?.policy && <p role="alert" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-[10px] text-amber-100">The stored rule uses a non-current scale. Marks-based classifications remain insufficient until the reviewed 12/25 policy migration is applied.</p>}
    {!data && !error && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading student evidence…</div>}
    {subject && <section className="surface rounded-xl p-4 sm:p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <label className="relative min-w-48 flex-1"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" /><span className="sr-only">Search students</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, ID, or class" className="form-input pl-9" /></label>
        <label className="text-[10px] text-slate-400">Evidence status<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="form-input mt-1 min-w-48"><option value="">All statuses</option><option value="LOW">Below support threshold</option><option value="NOT_LOW">Not below threshold</option><option value="INSUFFICIENT_EVIDENCE">Insufficient evidence</option></select></label>
      </div>
      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-rose-200/10 bg-rose-200/[.025] p-3"><p className="text-[9px] text-slate-500">FA-1 below 12/25</p><p className="mt-1 text-lg font-semibold text-rose-100">{subject.counts.LOW}</p></div>
        <div className="rounded-lg border border-white/[.08] p-3"><p className="text-[9px] text-slate-500">Not below threshold</p><p className="mt-1 text-lg font-semibold text-slate-100">{subject.counts.NOT_LOW}</p></div>
        <div className="rounded-lg border border-amber-200/10 bg-amber-200/[.025] p-3"><p className="text-[9px] text-slate-500">Insufficient evidence</p><p className="mt-1 text-lg font-semibold text-amber-100">{subject.counts.INSUFFICIENT_EVIDENCE}</p></div>
      </div>
      {!filteredStudents.length && <Empty title="No students match this view" detail="Change the search or evidence-status filter." />}
      {!!filteredStudents.length && <div className="overflow-x-auto">
        <table className="w-full min-w-[700px] border-collapse text-left">
          <thead><tr className="border-b border-white/[.08] text-[9px] uppercase tracking-wider text-slate-500"><th className="px-3 py-2">Student</th><th className="px-3 py-2">First internal assessment</th><th className="px-3 py-2">Subject status</th><th className="px-3 py-2">Official result</th><th className="px-3 py-2"><span className="sr-only">Action</span></th></tr></thead>
          <tbody>{filteredStudents.map((student) => <tr key={student.id} className="border-b border-white/[.05] align-top last:border-0">
            <td className="px-3 py-3"><p className="text-xs font-medium text-white">{student.full_name}</p><p className="mt-1 font-mono text-[9px] text-slate-500">{student.student_id}{student.section ? ` · ${student.section}` : ''}</p></td>
            <td className="px-3 py-3 text-[10px] text-slate-300">{student.latest_assessment
              ? <><p>{student.latest_assessment.assessment}</p><p className="mt-1">{student.latest_assessment.score ?? '—'} / {student.latest_assessment.maximum_marks ?? '—'} · normalized {student.latest_assessment.normalized_score === null ? 'Insufficient evidence' : `${student.latest_assessment.normalized_score.toFixed(2)}/${data.policy?.threshold_maximum ?? '—'}`}</p><p className="mt-1 text-slate-500">{student.latest_assessment.recorded_at ? new Date(student.latest_assessment.recorded_at).toLocaleDateString() : 'Date unavailable'}</p></>
              : <span className="text-amber-100">No unambiguous, verified latest assessment</span>}</td>
            <td className="px-3 py-3"><span className={`inline-flex rounded-full border px-2.5 py-1 text-[9px] font-medium ${student.status === 'LOW' ? 'border-rose-200/20 bg-rose-200/[.05] text-rose-100' : student.status === 'INSUFFICIENT_EVIDENCE' ? 'border-amber-200/20 text-amber-100' : 'border-white/10 text-slate-300'}`}>{statusLabel(student.status)}</span><p className="mt-1 max-w-xs text-[9px] leading-4 text-slate-500">{student.reason}</p></td>
            <td className="px-3 py-3 text-[10px]">{student.official_failure === true ? <span className="text-rose-100">Official FAIL recorded</span> : student.latest_assessment?.official_result ? <span className="text-slate-300">Official {student.latest_assessment.official_result}</span> : <span className="text-slate-500">Not recorded</span>}</td>
            <td className="px-3 py-3"><Link prefetch={false} href={`/teacher/recovery/subjects/${subject.id}/students/${student.id}`} className="inline-flex min-h-8 items-center gap-1 rounded-md bg-rose-600 px-2.5 text-[9px] font-semibold text-white hover:bg-rose-500">Open workspace <ArrowRight size={11} /></Link></td>
          </tr>)}</tbody>
        </table>
      </div>}
    </section>}
    {subject && <SubjectModuleRecoveryPanel studentId={requestedStudentId ?? undefined} initialSubjectId={subject.id} />}
    {subject && requestedStudentId && !filteredStudents.length && !search && <p role="status" className="text-xs text-amber-100">The selected student has no matching subject record in your assigned class.</p>}
  </div>;
}

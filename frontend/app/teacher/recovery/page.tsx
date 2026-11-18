'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, BookOpenCheck, CircleAlert, Search, ShieldCheck, UsersRound } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useMemo } from 'react';
import { Empty, ErrorNotice, Metric, RiskBadge } from '@/components/ui';
import { api } from '@/lib/api';
import { strictRecoveryEligibleSubjects } from '@/lib/recovery-eligibility';

type SubjectFocus = {
  id: number;
  code: string;
  name: string;
  students: {
    id: number;
    status: string;
    latest_assessments?: {
      recorded_at: string | null;
      evidence_status: string;
      maximum_marks_verified: boolean;
    }[];
    latest_assessment: {
      evidence_status: string;
      maximum_marks_verified: boolean;
      normalized_score: number | null;
      recorded_at: string | null;
    } | null;
  }[];
  counts: Record<'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE', number>;
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
  subjects: SubjectFocus[];
};
type RiskStudent = {
  id: number;
  student_id: string;
  full_name: string;
  section: string | null;
  course_name: string | null;
  risk_category: string;
  indicators: string[];
  subjects: {
    fa1: number | null;
    fa1_observed_at: string | null;
    temporal_evidence_verified: boolean;
    attendance_rate: number | null;
  }[];
};
type RiskOverview = { items: RiskStudent[]; total: number };

export default function TeacherRecoveryPage() {
  const [data, setData] = useState<FocusResponse | null>(null);
  const [directory, setDirectory] = useState<RiskOverview | null>(null);
  const [directoryError, setDirectoryError] = useState('');
  const [search, setSearch] = useState('');
  const [studentId, setStudentId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const reduceMotion = useReducedMotion() ?? false;

  useEffect(() => {
    const requestedStudentId = new URLSearchParams(window.location.search).get('student_id');
    if (requestedStudentId && Number.isInteger(Number(requestedStudentId)) && Number(requestedStudentId) > 0) {
      setStudentId(Number(requestedStudentId));
    }
    api<FocusResponse>('/api/v1/teacher/subject-focus?first_internal_only=true')
      .then(setData)
      .catch((requestError: Error) => setError(requestError.message));
    api<RiskOverview>('/api/v1/teacher/student-risk-overview')
      .then(setDirectory)
      .catch((requestError: Error) => setDirectoryError(requestError.message));
  }, []);

  const visibleStudents = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return (directory?.items ?? []).filter((student) => (
      !query
      || [student.full_name, student.student_id, student.section, student.course_name]
        .some((value) => value?.toLocaleLowerCase().includes(query))
    ));
  }, [directory, search]);

  const totals = data?.subjects.reduce((result, subject) => ({
    low: result.low + subject.counts.LOW,
    insufficient: result.insufficient + subject.counts.INSUFFICIENT_EVIDENCE,
    students: result.students + subject.students.length,
  }), { low: 0, insufficient: 0, students: 0 }) ?? { low: 0, insufficient: 0, students: 0 };

  return <div className="fade-up space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="eyebrow">Teacher recovery · assigned students</p><h1 className="mt-2 text-3xl font-semibold tracking-[-.035em] text-white sm:text-[2.1rem]">Recovery directory</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">Find assigned students, review recorded risk indicators, and open their verified FA-1 recovery profile.</p></div>
      <Link href="/teacher/recovery/roadmaps" className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 transition hover:border-rose-300/25"><ShieldCheck size={14} />Roadmap review & monitoring</Link>
    </div>
    <section aria-label="Assigned student recovery directory" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="eyebrow">Student-first recovery</p><h2 className="mt-1 text-base font-semibold text-white">Assigned students</h2></div>
        <label className="relative block w-full sm:w-72">
          <span className="sr-only">Search assigned students</span>
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, ID, class or course" className="form-input pl-9" />
        </label>
      </div>
      {directoryError && <ErrorNotice message={directoryError} />}
      {!directory && !directoryError && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading all students in your assigned classes…</div>}
      {directory && !visibleStudents.length && <div className="surface rounded-xl p-5"><Empty title={search ? 'No students match this search' : 'No assigned students available'} detail={search ? 'Try another name, student ID, class, or course.' : 'Students appear here when they are in a class assigned to you.'} /></div>}
      {!!visibleStudents.length && <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {visibleStudents.map((student, index) => {
          const eligibleCount = strictRecoveryEligibleSubjects(data, student.id).length;
          const fa1Values = student.subjects.filter((subject) => subject.fa1 !== null).length;
          const datedValues = student.subjects.filter((subject) => subject.fa1 !== null && subject.fa1_observed_at !== null).length;
          const verifiedDates = student.subjects.filter((subject) => (
            subject.fa1 !== null && subject.fa1_observed_at !== null && subject.temporal_evidence_verified
          )).length;
          const attendanceRecords = student.subjects.filter((subject) => subject.attendance_rate !== null).length;
          return <motion.article
            key={student.id}
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.2, delay: reduceMotion ? 0 : index * 0.025 }}
            className="surface rounded-xl p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-rose-200/15 bg-rose-200/[.05] text-rose-100"><UsersRound size={16} /></span>
                <div className="min-w-0"><h3 className="truncate text-sm font-semibold text-white">{student.full_name}</h3><p className="mt-1 truncate text-[10px] text-slate-500">{student.student_id}{student.section ? ` · Class ${student.section}` : ''}{student.course_name ? ` · ${student.course_name}` : ''}</p></div>
              </div>
              <RiskBadge level={student.risk_category} />
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-2 border-y border-white/[.07] py-3 text-center sm:grid-cols-4">
              <div><dt className="text-[9px] text-slate-500">Eligible FA-1 subjects</dt><dd className="mt-1 text-sm font-semibold text-rose-100">{eligibleCount}</dd></div>
              <div><dt className="text-[9px] text-slate-500">FA-1 values</dt><dd className="mt-1 text-sm font-semibold text-slate-200">{fa1Values}</dd></div>
              <div><dt className="text-[9px] text-slate-500">Dated values</dt><dd className="mt-1 text-sm font-semibold text-slate-200">{datedValues}</dd></div>
              <div><dt className="text-[9px] text-slate-500">Verified dates</dt><dd className="mt-1 text-sm font-semibold text-slate-200">{verifiedDates}</dd></div>
            </dl>
            <p className="mt-3 text-[9px] font-medium uppercase tracking-[.08em] text-slate-500">Recorded indicators</p>
            <ul className="mt-1 space-y-1">{student.indicators.map((indicator) => <li key={indicator} className="text-[10px] leading-4 text-slate-400">{indicator}</li>)}</ul>
            <Link href={`/teacher/recovery/students/${student.id}`} className="mt-4 inline-flex min-h-9 items-center gap-2 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white transition hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
              Open recovery profile <ArrowRight size={13} />
            </Link>
          </motion.article>;
        })}
      </div>}
      {directory && <p className="text-right text-[10px] text-slate-500">Showing {visibleStudents.length} of {directory.total} assigned students</p>}
    </section>
    {error && <ErrorNotice message={error} />}
    {studentId && <div role="status" className="rounded-lg border border-rose-200/15 bg-rose-200/[.035] px-4 py-3 text-xs text-rose-100">Student #{studentId} was selected from the risk overview. Choose a subject to inspect that student’s evidence.</div>}
    {data?.policy?.active_for_classification
      ? <div className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[.08] bg-white/[.02] px-4 py-3 text-xs text-slate-300">
        <ShieldCheck size={14} className="text-emerald-200" />
        <span>Project subject-support rule v{data.policy.version}: scores strictly below {data.policy.threshold_score}/{data.policy.threshold_maximum} after proportional normalization.</span>
        <span className="text-slate-500">This support indicator is not an official pass/fail decision.</span>
      </div>
      : data?.policy
        ? <div role="alert" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-xs text-amber-100">Stored subject-support policy v{data.policy.version} uses {data.policy.threshold_score}/{data.policy.threshold_maximum}. The required normalized scale is 25; marks-based classifications remain insufficient until the reviewed policy migration is applied.</div>
        : data && <div role="status" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] px-4 py-3 text-xs text-amber-100">No authorized subject-support policy is configured. Automatic subject labels remain inactive.</div>}

    <section aria-label="Subject support summary" className="flex flex-wrap items-end justify-between gap-3">
      <div><p className="eyebrow">Verified first-internal evidence</p><h2 className="mt-1 text-base font-semibold text-white">Subject support overview</h2></div>
      <div className="grid flex-1 gap-3 sm:grid-cols-3">
      <Metric label="Subjects" value={data?.subjects.length ?? '—'} detail="Linked to your assigned courses" color="amber" />
      <Metric label="FA-1 below support threshold" value={data ? totals.low : '—'} detail="Verified first-internal result below configured rule" color="rose" />
      <Metric label="Insufficient evidence" value={data ? totals.insufficient : '—'} detail="Missing, ambiguous, or unverified first-internal evidence" color="amber" />
      </div>
    </section>

    {data === null && !error && <div role="status" className="surface rounded-xl p-5 text-xs text-slate-400">Loading persisted subject evidence…</div>}
    {data && !data.subjects.length && <div className="surface rounded-xl p-5"><Empty title="No subject workspaces available" detail="Subjects are listed when they are linked to your assigned courses or department." /></div>}
    {data && !!data.subjects.length && <section aria-label="Subjects" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {data.subjects.map((subject, index) => <motion.article
        key={subject.id}
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduceMotion ? 0 : 0.2, delay: reduceMotion ? 0 : index * 0.035 }}
        className="surface rounded-xl p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-lg border border-rose-200/15 bg-rose-200/[.05] text-rose-100"><BookOpenCheck size={18} /></span>
            <div className="min-w-0"><p className="eyebrow">{subject.code}</p><h2 className="mt-1 truncate text-sm font-semibold text-white">{subject.name}</h2></div>
          </div>
          {subject.counts.INSUFFICIENT_EVIDENCE > 0 && <CircleAlert size={15} className="shrink-0 text-amber-200" aria-label="Some students have insufficient evidence" />}
        </div>
        <dl className="mt-5 grid grid-cols-3 gap-2 border-y border-white/[.07] py-3 text-center">
          <div><dt className="text-[9px] text-slate-500">Below threshold</dt><dd className="mt-1 text-sm font-semibold text-rose-100">{subject.counts.LOW}</dd></div>
          <div><dt className="text-[9px] text-slate-500">Not below</dt><dd className="mt-1 text-sm font-semibold text-slate-200">{subject.counts.NOT_LOW}</dd></div>
          <div><dt className="text-[9px] text-slate-500">Insufficient</dt><dd className="mt-1 text-sm font-semibold text-amber-100">{subject.counts.INSUFFICIENT_EVIDENCE}</dd></div>
        </dl>
        <div className="mt-4 flex items-center justify-between gap-3">
          <span className="text-[10px] text-slate-500">{subject.students.length} assigned student{subject.students.length === 1 ? '' : 's'}</span>
          <Link href={`/teacher/recovery/subjects/${subject.id}${studentId ? `?student_id=${studentId}` : ''}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white transition hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
            Open subject <ArrowRight size={13} />
          </Link>
        </div>
      </motion.article>)}
    </section>}
  </div>;
}

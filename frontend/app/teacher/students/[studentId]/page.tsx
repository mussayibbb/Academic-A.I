
'use client';

import { motion } from 'framer-motion';
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  ChevronLeft,
  Clock3,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Metric, RiskBadge } from '@/components/ui';
import { api } from '@/lib/api';
import { formatRiskProbability } from '@/lib/risk';

type Student = {
  id: number;
  student_id: string;
  full_name: string;
  program?: string | null;
  year?: string | null;
  semester?: string | null;
  section?: string | null;
  department?: string | null;
  branch?: string | null;
  data_origin: string;
  risk_level: string;
  risk_probability: number;
  risk_status?: string;
  risk_status_message?: string | null;
  previous_gpa: number | null;
  current_gpa: number | null;
  previous_cgpa?: number | null;
  current_cgpa?: number | null;
  attendance: number;
  assessment: number;
  assignments_completed: number;
};

type Observation = {
  metric: string;
  value: number;
  observed_at: string;
};

type Submission = {
  id: number;
  assignment_id: number;
  status: string;
  grade?: number | null;
  feedback?: string | null;
  submitted_at?: string | null;
  file_name?: string | null;
};

type SubjectRecord = {
  name: string;
  fa1: number | null;
  fa1_observed_at: string | null;
  fa2: number | null;
  fa2_observed_at: string | null;
  fa_average: number | null;
  assignment_mark: number | null;
  assignment_observed_at: string | null;
  internal_mark: number | null;
  attendance_rate: number | null;
  attendance_observed_at: string | null;
};

type SubjectAssignment = {
  id: number;
  title: string;
  subject_code: string;
  status: string;
  due_date: string | null;
  max_marks: number;
  submission_status: string;
  uploaded: boolean;
  file_name: string | null;
  grade: number | null;
  feedback: string | null;
  submitted_at: string | null;
};

type Detail = {
  student: Student;
  momentum: Observation[];
  submissions: Submission[];
  risk_history: { risk_level: string; recorded_at: string }[];
  academic_records: {
    subjects: Record<string, SubjectRecord>;
    risk_scope: string;
    academic_period: string | null;
    academic_periods: string[];
  };
};

type SubjectFocusRow = {
  id: number;
  status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
  reason: string;
  official_failure: boolean | null;
  support_triggers: ('LOW_SCORE' | 'OFFICIAL_FAIL')[];
};

type TeacherSubjectFocus = {
  subjects: {
    id: number;
    code: string;
    name: string;
    students: SubjectFocusRow[];
  }[];
};

type SummarySignal = {
  feature: string;
  value: number;
  contribution: number;
};

type RiskSummary =
  | { status: 'ready'; provider: string; selected_signals: SummarySignal[] }
  | { status: 'unavailable'; provider: string | null; error: string };

type Explanation = {
  prediction: {
    risk_level: string;
    risk_probability: number;
    risk_probability_semantics: string;
    model_version: string;
  };
  explanation: {
    target: string;
    target_class: string;
    base_value: number;
    contributions: Record<string, number>;
    feature_values: Record<string, number>;
    waterfall: {
      feature: string;
      value: number;
      contribution: number;
    }[];
    summary: string[];
  };
  risk_summary: RiskSummary;
  risk_status?: string;
};

type TabName =
  | 'Overview'
  | 'EVS'
  | 'CN'
  | 'TOC'
  | 'RM'
  | 'SE'
  | 'Academic performance'
  | 'Assignments'
  | 'Learning activity'
  | 'Risk analysis'
  | 'SHAP';

const subjectTabs: TabName[] = ['EVS', 'CN', 'TOC', 'RM', 'SE'];

const tabs: TabName[] = [
  'Overview',
  ...subjectTabs,
  'Academic performance',
  'Assignments',
  'Learning activity',
  'Risk analysis',
  'SHAP',
];

function formatAcademicValue(value: number | null | undefined) {
  return value == null ? 'Not recorded' : value.toFixed(2);
}

export default function Student360Page() {
  const params = useParams<{ studentId: string }>();

  const [detail, setDetail] = useState<Detail | null>(null);
  const [tab, setTab] = useState<TabName>('Overview');
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [loadingExplanation, setLoadingExplanation] = useState(false);
  const [subjectAssignments, setSubjectAssignments] = useState<SubjectAssignment[]>([]);
  const [subjectFocus, setSubjectFocus] = useState<TeacherSubjectFocus | null>(null);
  const [subjectFocusError, setSubjectFocusError] = useState('');
  const [error, setError] = useState('');

  const changeAcademicPeriod = async (academicPeriod: string) => {
    if (!detail) return;

    try {
      const query = academicPeriod
        ? `?academic_period=${encodeURIComponent(academicPeriod)}`
        : '';

      const academicRecords = await api<Detail['academic_records']>(
        `/api/v1/students/${detail.student.id}/academic-records${query}`
      );

      setDetail((current) =>
        current ? { ...current, academic_records: academicRecords } : current
      );
    } catch (requestError) {
      setError((requestError as Error).message);
    }
  };

  useEffect(() => {
    api<Detail>(`/api/v1/students/${encodeURIComponent(params.studentId)}`)
      .then(setDetail)
      .catch((requestError: Error) => setError(requestError.message));
  }, [params.studentId]);

  const currentStudentId = detail?.student.id;

  useEffect(() => {
    if (currentStudentId == null) return;

    let active = true;

    api<TeacherSubjectFocus>(
      `/api/v1/teacher/subject-focus?student_id=${currentStudentId}&first_internal_only=true`
    )
      .then((result) => {
        if (active) setSubjectFocus(result);
      })
      .catch((requestError: Error) => {
        if (active) setSubjectFocusError(requestError.message);
      });

    return () => {
      active = false;
    };
  }, [currentStudentId]);

  const openTab = async (next: TabName) => {
    if (
      next === 'SHAP' &&
      detail &&
      !['available', 'unvalidated-demo-model'].includes(
        detail.student.risk_status ?? ''
      )
    ) {
      return;
    }

    setTab(next);

    if (subjectTabs.includes(next) && detail) {
      try {
        setSubjectAssignments(
          await api<SubjectAssignment[]>(
            `/api/v1/students/${detail.student.id}/subjects/${next}/assignments`
          )
        );
      } catch (requestError) {
        setError((requestError as Error).message);
      }
      return;
    }

    if (next !== 'SHAP' || explanation || loadingExplanation) return;

    setLoadingExplanation(true);

    try {
      const result = await api<Explanation>(
        `/api/v1/students/${params.studentId}/shap`
      );

      setExplanation(result);

      setDetail((current) =>
        current
          ? {
              ...current,
              student: {
                ...current.student,
                risk_level: result.prediction.risk_level,
                risk_probability: result.prediction.risk_probability,
              },
            }
          : current
      );
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setLoadingExplanation(false);
    }
  };

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-rose-300/20 bg-rose-300/5 p-4 text-sm text-rose-100"
      >
        {error}
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-36 rounded-xl" />
        <div className="skeleton h-72 rounded-xl" />
      </div>
    );
  }

  const { student } = detail;

  const hasCurrentRisk =
    student.risk_status === 'available' ||
    student.risk_status === 'unvalidated-demo-model';

  const riskLabel = hasCurrentRisk ? student.risk_level : 'UNAVAILABLE';

  const riskText = !hasCurrentRisk
    ? 'Unavailable'
    : student.risk_status === 'unvalidated-demo-model'
      ? 'Unvalidated demo preview'
      : formatRiskProbability(student.risk_level, student.risk_probability);

  const metricGroups = detail.momentum.reduce<Record<string, Observation[]>>(
    (groups, item) => {
      (groups[item.metric] ??= []).push(item);
      return groups;
    },
    {}
  );

  const subjectRecord = subjectTabs.includes(tab)
    ? detail.academic_records.subjects[tab]
    : undefined;

  const waterfall = explanation?.explanation.waterfall ?? [];

  const maxContribution = waterfall.reduce(
    (maximum, item) => Math.max(maximum, Math.abs(item.contribution)),
    0
  );

  const gpaComparison =
    student.previous_gpa == null || student.current_gpa == null
      ? 'Comparison unavailable'
      : student.current_gpa >= student.previous_gpa
        ? 'Above previous term'
        : 'Below previous term';

  return (
    <div className="fade-up space-y-6">
      <Link
        href="/teacher/students"
        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-cyan-200"
      >
        <ChevronLeft size={15} /> Student directory
      </Link>

      <p className="text-xs text-slate-500">
        Risk probability is confidence in the displayed class; SHAP explains
        that same class probability.
      </p>

      {subjectRecord && (
        <label className="block max-w-xs text-xs text-slate-400">
          Academic period
          <select
            value={detail.academic_records.academic_period ?? ''}
            onChange={(event) => void changeAcademicPeriod(event.target.value)}
            className="form-input"
          >
            <option value="">No period records</option>
            {detail.academic_records.academic_periods.map((period) => (
              <option key={period} value={period}>
                {period}
              </option>
            ))}
          </select>
        </label>
      )}

      <section className="surface relative overflow-hidden rounded-xl p-5 sm:p-7">
        <div className="absolute right-0 top-0 h-40 w-1/3 border-l border-white/[.035] bg-gradient-to-bl from-cyan-300/[.07] to-transparent" />

        <div className="relative flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="eyebrow">
              Student 360 · {student.department ?? 'Academic profile'}
            </p>

            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">
              {student.full_name}
            </h1>

            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
              <span className="font-mono">{student.student_id}</span>
              <span>{student.program ?? 'Program not set'}</span>
              <span>Branch {student.branch ?? '—'}</span>
              <span>Year {student.year ?? '—'}</span>
              <span>Semester {student.semester ?? '—'}</span>
              <span>Section {student.section ?? '—'}</span>
            </div>

            <span className="mt-3 inline-flex rounded-md border border-white/10 bg-white/[.025] px-2 py-1 text-[9px] text-slate-300">
              Data origin · {student.data_origin}
            </span>
          </div>

          <div className="flex items-center gap-3">
            <RiskBadge level={riskLabel} />
            <div className="text-right">
              <div className="text-2xl font-semibold text-white">{riskText}</div>
              <div className="text-[10px] uppercase tracking-[.1em] text-slate-500">
                Risk assessment status
              </div>
            </div>
          </div>
        </div>

        {student.risk_status_message && (
          <p
            role="status"
            className="relative mt-4 rounded-lg border border-amber-200/15 bg-amber-200/[.035] p-3 text-xs leading-5 text-amber-100"
          >
            {student.risk_status_message}
          </p>
        )}

        <div className="relative mt-6 grid gap-3 sm:grid-cols-3">
          <Metric
            label="Attendance"
            value={`${student.attendance.toFixed(0)}%`}
            detail="Recorded academic observations"
            color={student.attendance < 75 ? 'rose' : 'cyan'}
          />
          <Metric
            label="Assessment"
            value={`${student.assessment.toFixed(0)}%`}
            detail="Current assessment average"
            color="amber"
          />
          <Metric
            label="Assignments"
            value={student.assignments_completed}
            detail="Completed engagements"
            color="mint"
          />
        </div>
      </section>

      <section aria-label="Subject recovery support" className="surface rounded-xl p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">Verified subject evidence</p>
            <h2 className="mt-1 text-sm font-semibold text-white">
              Subject recovery workspaces
            </h2>
            <p className="mt-1 text-[10px] text-slate-500">
              Only first-internal low scores and explicit official FAIL results
              trigger automatic support.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <Link
              href={`/teacher/recovery/students/${student.id}`}
              className="inline-flex min-h-9 items-center gap-1 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white hover:bg-rose-500"
            >
              Recovery profile <ArrowRight size={12} />
            </Link>

            <Link
              href={`/teacher/recovery/roadmaps?student_id=${student.id}`}
              className="inline-flex min-h-9 items-center gap-1 rounded-lg border border-white/10 px-3 text-[10px] text-slate-200 hover:border-rose-200/20"
            >
              Review roadmaps <ArrowRight size={12} />
            </Link>
          </div>
        </div>

        {subjectFocusError && (
          <p
            role="alert"
            className="mt-3 rounded-lg border border-rose-200/15 bg-rose-200/[.035] p-3 text-xs text-rose-100"
          >
            Subject support evidence could not be loaded: {subjectFocusError}
          </p>
        )}

        {!subjectFocus && !subjectFocusError && (
          <p role="status" className="mt-3 text-xs text-slate-500">
            Loading subject-specific support evidence…
          </p>
        )}

        {subjectFocus &&
          (() => {
            const rows = subjectFocus.subjects.flatMap((subject) => {
              const row = subject.students.find(
                (candidate) => candidate.id === student.id
              );
              return row ? [{ subject, row }] : [];
            });

            const actionable = rows.filter(
              ({ row }) => row.support_triggers.length > 0
            );

            const insufficient = rows.filter(
              ({ row }) => row.status === 'INSUFFICIENT_EVIDENCE'
            );

            return (
              <>
                {actionable.length > 0 ? (
                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    {actionable.map(({ subject, row }) => (
                      <article
                        key={subject.id}
                        className="rounded-lg border border-rose-200/15 bg-rose-200/[.025] p-4"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="eyebrow">{subject.code}</p>
                            <h3 className="mt-1 text-xs font-semibold text-white">
                              {subject.name}
                            </h3>
                          </div>

                          <span className="rounded-full border border-rose-200/20 px-2 py-1 text-[9px] text-rose-100">
                            {row.support_triggers.includes('OFFICIAL_FAIL')
                              ? 'Official FAIL'
                              : 'Below threshold'}
                          </span>
                        </div>

                        <p className="mt-2 text-[10px] leading-5 text-slate-400">
                          {row.reason}
                        </p>

                        <Link
                          href={`/teacher/recovery/subjects/${subject.id}/students/${student.id}`}
                          className="mt-3 inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                        >
                          Open {subject.name} workspace <ArrowRight size={12} />
                        </Link>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className="mt-3 text-xs text-slate-400">
                    No subject has a verified low first-internal score or
                    explicit official FAIL result.
                  </p>
                )}

                {insufficient.length > 0 && (
                  <p className="mt-3 text-[10px] leading-5 text-amber-100">
                    {insufficient.length} subject
                    {insufficient.length === 1 ? ' has' : 's have'} insufficient
                    verified evidence; automatic eligibility is not inferred.
                  </p>
                )}
              </>
            );
          })()}
      </section>

      <div className="border-b border-white/10">
        <div
          role="tablist"
          aria-label="Student details"
          className="flex gap-1 overflow-x-auto"
        >
          {tabs.map((item) => (
            <button
              key={item}
              role="tab"
              aria-selected={tab === item}
              disabled={item === 'SHAP' && !hasCurrentRisk}
              onClick={() => void openTab(item)}
              className={`whitespace-nowrap border-b-2 px-3 py-3 text-xs transition disabled:cursor-not-allowed disabled:opacity-40 ${
                tab === item
                  ? 'border-cyan-200 text-cyan-100'
                  : 'border-transparent text-slate-500 hover:text-slate-200'
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {tab === 'SHAP' && explanation && (
        <p className="text-xs text-slate-500">
          SHAP target class: {explanation.explanation.target_class} (
          {explanation.explanation.target}).
        </p>
      )}

      {tab === 'Overview' && (
        <section className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          <div className="surface rounded-xl p-5">
            <h2 className="text-sm font-semibold">Academic progress</h2>
            <p className="mt-1 text-xs text-slate-500">
              Previous and current academic results
            </p>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="surface-soft rounded-lg p-4">
                <div className="text-xs text-slate-500">Previous GPA · /4</div>
                <div className="mt-2 text-2xl font-semibold text-slate-300">
                  {formatAcademicValue(student.previous_gpa)}
                </div>
              </div>

              <div className="rounded-lg border border-cyan-200/15 bg-cyan-200/[.04] p-4">
                <div className="text-xs text-cyan-100/70">Current GPA · /4</div>
                <div className="mt-2 text-2xl font-semibold text-cyan-100">
                  {formatAcademicValue(student.current_gpa)}
                </div>
                <div className="mt-1 text-[10px] text-slate-500">
                  {gpaComparison}
                </div>
              </div>

              <div className="surface-soft rounded-lg p-4">
                <div className="text-xs text-slate-500">Previous CGPA · /10</div>
                <div className="mt-2 text-2xl font-semibold text-slate-300">
                  {formatAcademicValue(student.previous_cgpa)}
                </div>
              </div>

              <div className="rounded-lg border border-emerald-200/15 bg-emerald-200/[.04] p-4">
                <div className="text-xs text-emerald-100/70">Current CGPA · /10</div>
                <div className="mt-2 text-2xl font-semibold text-emerald-100">
                  {formatAcademicValue(student.current_cgpa)}
                </div>
              </div>
            </div>
          </div>

          <div className="surface rounded-xl p-5">
            <h2 className="text-sm font-semibold">Recent academic signals</h2>

            <div className="mt-3 space-y-2">
              {Object.entries(metricGroups).map(([metric, rows]) => {
                const latest = rows.at(-1);
                const first = rows[0];
                const up =
                  !!latest && !!first && latest.value >= first.value;

                return (
                  <div
                    key={metric}
                    className="flex items-center gap-3 border-t border-white/6 py-3 first:border-0"
                  >
                    <span
                      className={`grid size-8 place-items-center rounded-lg ${
                        up
                          ? 'bg-emerald-300/10 text-emerald-200'
                          : 'bg-rose-300/10 text-rose-200'
                      }`}
                    >
                      {up ? (
                        <ArrowUpRight size={15} />
                      ) : (
                        <ArrowDownRight size={15} />
                      )}
                    </span>

                    <div className="flex-1">
                      <div className="text-xs font-medium capitalize text-slate-200">
                        {metric.replaceAll('_', ' ')}
                      </div>
                      <div className="mt-1 text-[10px] text-slate-500">
                        {rows.length} dated observations
                      </div>
                    </div>

                    <div className="text-xs text-slate-300">
                      {latest?.value.toFixed(0) ?? '—'}
                    </div>
                  </div>
                );
              })}

              {!Object.keys(metricGroups).length && (
                <p className="py-4 text-xs text-slate-500">
                  No dated observations are recorded.
                </p>
              )}
            </div>
          </div>
        </section>
      )}

      {subjectRecord && (
        <section className="surface rounded-xl p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="eyebrow">{tab} · subject record</p>
              <h2 className="mt-1 text-lg font-semibold">
                {subjectRecord.name}
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                Subject-specific marks and attendance are not inputs to the
                current global risk model.
              </p>
            </div>

            <div className="text-right">
              <RiskBadge level={riskLabel} />
              <div className="mt-1 text-[10px] text-slate-500">
                Global model · {riskText}
              </div>
              <button
                type="button"
                disabled={!hasCurrentRisk}
                onClick={() => void openTab('SHAP')}
                className="mt-2 text-xs text-cyan-200 hover:underline disabled:opacity-40"
              >
                View global SHAP explanation
              </button>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {[
              ['FA1 /25', subjectRecord.fa1],
              ['FA2 /25', subjectRecord.fa2],
              ['FA average /25', subjectRecord.fa_average],
              ['Assignment /25', subjectRecord.assignment_mark],
              ['Internal /50', subjectRecord.internal_mark],
              ['Attendance %', subjectRecord.attendance_rate],
            ].map(([label, value]) => (
              <div key={label} className="border-t border-white/8 py-3">
                <div className="text-[10px] text-slate-500">{label}</div>
                <div className="mt-1 text-lg font-semibold text-slate-100">
                  {value == null ? '—' : value}
                </div>
              </div>
            ))}
          </div>

          <h3 className="mt-4 border-t border-white/8 pt-4 text-xs font-semibold">
            Subject assignments
          </h3>

          <div className="mt-2 divide-y divide-white/6">
            {subjectAssignments.map((item) => (
              <div
                key={item.id}
                className="grid gap-2 py-3 text-xs sm:grid-cols-[1fr_auto_auto]"
              >
                <div>
                  <div className="text-slate-200">{item.title}</div>
                  <div className="mt-1 text-[10px] text-slate-500">
                    Due{' '}
                    {item.due_date
                      ? new Date(item.due_date).toLocaleDateString()
                      : 'not scheduled'}{' '}
                    ·{' '}
                    {item.file_name ??
                      (item.uploaded ? 'File uploaded' : 'No file uploaded')}
                  </div>
                </div>

                <span className="capitalize text-slate-400">
                  {item.submission_status.replaceAll('_', ' ')}
                </span>

                <span className="text-slate-300">
                  {item.grade == null
                    ? `— / ${item.max_marks}`
                    : `${item.grade} / ${item.max_marks}`}
                </span>

                {item.feedback && (
                  <p className="text-slate-500 sm:col-span-3">
                    {item.feedback}
                  </p>
                )}
              </div>
            ))}

            {!subjectAssignments.length && (
              <p className="py-4 text-xs text-slate-500">
                No assignments are linked to this subject yet.
              </p>
            )}
          </div>

          <p className="mt-3 text-[10px] text-slate-600">
            Subject-specific momentum and SHAP are unavailable because the
            existing model and observation schema are global.
          </p>
        </section>
      )}

      {tab === 'Academic performance' && (
        <section className="surface rounded-xl p-5">
          <h2 className="text-sm font-semibold">
            Academic performance over time
          </h2>

          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            {['assessment', 'attendance'].map((metric) => (
              <div key={metric} className="surface-soft rounded-lg p-4">
                <div className="flex justify-between">
                  <span className="text-xs capitalize text-slate-400">
                    {metric}
                  </span>
                  <span className="text-xs text-slate-500">%</span>
                </div>

                <div className="mt-4 flex h-36 items-end gap-1.5">
                  {(metricGroups[metric] ?? []).map((item, index) => (
                    <div
                      key={`${item.observed_at}-${index}`}
                      title={`${new Date(item.observed_at).toLocaleDateString()}: ${item.value.toFixed(1)}%`}
                      className="group relative flex-1 rounded-t-sm bg-gradient-to-t from-cyan-500/60 to-cyan-200/80"
                      style={{ height: `${Math.max(4, item.value)}%` }}
                    >
                      <span className="absolute -top-5 left-1/2 hidden -translate-x-1/2 text-[9px] text-slate-300 group-hover:block">
                        {Math.round(item.value)}
                      </span>
                    </div>
                  ))}
                </div>

                <div className="mt-2 flex justify-between text-[9px] text-slate-600">
                  <span>Earlier</span>
                  <span>Latest</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'Assignments' && (
        <section className="surface rounded-xl p-5">
          <h2 className="text-sm font-semibold">Assignment engagement</h2>

          <div className="mt-4 divide-y divide-white/6">
            {detail.submissions.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center gap-3 py-3"
              >
                <span className="size-2 rounded-full bg-cyan-200" />
                <div className="flex-1 text-xs text-slate-200">
                  Assignment {item.assignment_id}
                </div>
                <span className="text-xs text-slate-400">{item.status}</span>
                <span className="text-xs text-slate-300">
                  {item.grade == null ? 'Not graded' : `${item.grade} marks`}
                </span>
                <span className="text-[10px] text-slate-500">
                  {item.submitted_at
                    ? new Date(item.submitted_at).toLocaleString()
                    : 'No timestamp'}
                </span>
              </div>
            ))}

            {!detail.submissions.length && (
              <div className="py-8 text-center text-xs text-slate-500">
                No assignment submissions recorded.
              </div>
            )}
          </div>
        </section>
      )}

      {tab === 'Learning activity' && (
        <section className="surface rounded-xl p-5">
          <h2 className="text-sm font-semibold">Dated learning observations</h2>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[450px] text-left text-xs">
              <thead className="text-[10px] uppercase tracking-[.1em] text-slate-500">
                <tr>
                  <th className="pb-3">Signal</th>
                  <th className="pb-3">Value</th>
                  <th className="pb-3">Observed</th>
                </tr>
              </thead>

              <tbody>
                {detail.momentum.map((item, index) => (
                  <tr
                    key={`${item.metric}-${item.observed_at}-${index}`}
                    className="border-t border-white/6"
                  >
                    <td className="py-3 capitalize text-slate-300">
                      {item.metric.replaceAll('_', ' ')}
                    </td>
                    <td className="py-3 text-slate-200">
                      {item.value.toFixed(1)}
                    </td>
                    <td className="py-3 text-slate-500">
                      {new Date(item.observed_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!detail.momentum.length && (
            <div className="py-6 text-center text-xs text-slate-500">
              No learning activity observations are available.
            </div>
          )}
        </section>
      )}

      {tab === 'Risk analysis' && (
        <section className="surface rounded-xl p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-amber-100" />
            <h2 className="text-sm font-semibold">Risk history</h2>
          </div>

          <p className="mt-1 text-xs text-slate-500">
            Historical saved class labels; older entries may predate the
            current argmax policy.
          </p>

          <div className="mt-5 flex flex-wrap gap-3">
            {detail.risk_history.map((item, index) => (
              <div
                key={`${item.recorded_at}-${index}`}
                className="surface-soft min-w-44 rounded-lg p-4"
              >
                <div className="flex items-center justify-between">
                  <RiskBadge level={item.risk_level} />
                  <span className="text-[10px] text-slate-500">Historical</span>
                </div>

                <div className="mt-3 text-[10px] text-slate-500">
                  {new Date(item.recorded_at).toLocaleString()}
                </div>
              </div>
            ))}

            {!detail.risk_history.length && (
              <div className="text-xs text-slate-500">
                No risk history has been saved yet.
              </div>
            )}
          </div>
        </section>
      )}

      {tab === 'SHAP' && (
        <section className="surface rounded-xl p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="eyebrow">Model explanation</p>
              <h2 className="mt-1 text-lg font-semibold">
                What informs this classification?
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                SHAP contributions explain this student’s inputs to the
                predicted class probability, not causes.
              </p>
            </div>

            <span className="rounded-full border border-rose-300/15 bg-rose-300/[.05] px-3 py-1.5 text-[10px] text-rose-100">
              {explanation?.prediction.model_version ?? 'SHAP'}
            </span>
          </div>

          {student.risk_status === 'unvalidated-demo-model' && (
            <p
              role="status"
              className="mt-4 rounded-lg border border-amber-200/15 bg-amber-200/[.035] p-3 text-xs leading-5 text-amber-100"
            >
              Unvalidated DEMO DATA preview. This explanation is not saved to
              validated student risk history.
            </p>
          )}

          {loadingExplanation && (
            <div className="mt-5 flex items-center gap-2 text-xs text-slate-400">
              <RefreshCw size={14} className="animate-spin" />
              Calculating model explanation and reviewing verified evidence…
            </div>
          )}

          {explanation && (
            <>
              <div className="mt-5 rounded-lg border border-white/7 bg-black/10 px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                  <span>
                    SHAP target{' '}
                    <strong className="ml-1 text-white">
                      P({explanation.explanation.target_class})
                    </strong>
                  </span>

                  <strong className="text-white">
                    {formatRiskProbability(
                      explanation.prediction.risk_level,
                      explanation.prediction.risk_probability
                    )}
                  </strong>
                </div>

                <div className="mt-4 space-y-3">
                  {waterfall.map((item) => {
                    const magnitude =
                      maxContribution > 0
                        ? (Math.abs(item.contribution) / maxContribution) * 50
                        : 0;

                    const featureLabel = item.feature.replaceAll('_', ' ');

                    const contributionLabel =
                      item.contribution > 0
                        ? `+${item.contribution.toFixed(3)}`
                        : item.contribution.toFixed(3);

                    return (
                      <div
                        key={item.feature}
                        className="grid min-w-0 grid-cols-[minmax(0,1.1fr)_minmax(56px,1.5fr)_48px] items-center gap-2 sm:grid-cols-[minmax(0,1.2fr)_minmax(90px,2fr)_64px] sm:gap-3"
                      >
                        <div className="min-w-0">
                          <div className="break-words text-[11px] capitalize text-slate-300">
                            {featureLabel}
                          </div>
                          <div className="mt-0.5 text-[9px] text-slate-500">
                            Input{' '}
                            {new Intl.NumberFormat(undefined, {
                              maximumSignificantDigits: 5,
                            }).format(item.value)}
                          </div>
                        </div>

                        <div
                          className="relative h-2 min-w-0 rounded-full bg-white/[.06]"
                          aria-hidden="true"
                        >
                          <div
                            className={`absolute top-0 h-full rounded-full transition-[left,width] duration-500 motion-reduce:transition-none ${
                              item.contribution > 0
                                ? 'bg-rose-400'
                                : item.contribution < 0
                                  ? 'bg-slate-300'
                                  : 'bg-slate-500'
                            }`}
                            style={{
                              width: `${magnitude}%`,
                              left: `${
                                item.contribution < 0
                                  ? 50 - magnitude
                                  : 50
                              }%`,
                            }}
                          />
                          <span className="absolute left-1/2 top-[-3px] h-3.5 w-px bg-slate-500" />
                        </div>

                        <div
                          className={`text-right font-mono text-[10px] ${
                            item.contribution > 0
                              ? 'text-rose-200'
                              : 'text-slate-300'
                          }`}
                          aria-label={`${contributionLabel} contribution to P(${explanation.explanation.target_class})`}
                        >
                          {contributionLabel}
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-white/[.06] pt-3 text-[10px] text-slate-400">
                  <span className="inline-flex items-center gap-2">
                    <span className="size-2 rounded-full bg-rose-400" />
                    Increases P({explanation.explanation.target_class})
                  </span>
                  <span className="inline-flex items-center gap-2">
                    <span className="size-2 rounded-full bg-slate-300" />
                    Decreases P({explanation.explanation.target_class})
                  </span>
                  <span>Bar scale is relative to the largest contribution shown.</span>
                </div>
              </div>

              <div className="mt-4">
                <h3 className="text-xs font-semibold text-slate-200">
                  Largest model-attributed signals
                </h3>

                <ul className="mt-2 space-y-2">
                  {explanation.explanation.summary.map((item) => (
                    <li
                      key={item}
                      className="flex gap-2 text-xs leading-5 text-slate-400"
                    >
                      <span className="mt-1.5 size-1 shrink-0 rounded-full bg-rose-300" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="mt-5 rounded-lg border border-rose-300/15 bg-rose-300/[.035] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Sparkles size={15} className="text-rose-200" />
                    <h3 className="text-sm font-semibold text-white">
                      AI Risk Summary
                    </h3>
                  </div>

                  <span className="text-[10px] text-slate-400">
                    {explanation.risk_summary.provider ?? 'AI provider'}
                    {explanation.risk_summary.status === 'ready'
                      ? ' · evidence-selected'
                      : ' · unavailable'}
                  </span>
                </div>

                {explanation.risk_summary.status === 'ready' ? (
                  <>
                    <p className="mt-3 text-xs leading-5 text-slate-300">
                      The model predicts{' '}
                      <strong className="text-white">
                        {explanation.prediction.risk_level}
                      </strong>{' '}
                      with{' '}
                      {formatRiskProbability(
                        explanation.prediction.risk_level,
                        explanation.prediction.risk_probability
                      )}
                      . The configured AI selected these verified signals for
                      review:
                    </p>

                    <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                      {explanation.risk_summary.selected_signals.map(
                        (signal) => (
                          <li
                            key={signal.feature}
                            className="rounded-md border border-white/[.06] bg-black/[.12] p-3"
                          >
                            <div className="text-xs capitalize text-white">
                              {signal.feature.replaceAll('_', ' ')}
                            </div>

                            <div className="mt-1 font-mono text-[10px] text-slate-400">
                              Input{' '}
                              {new Intl.NumberFormat(undefined, {
                                maximumSignificantDigits: 5,
                              }).format(signal.value)}{' '}
                              <span
                                className={
                                  signal.contribution > 0
                                    ? 'text-rose-200'
                                    : 'text-slate-300'
                                }
                              >
                                · {signal.contribution > 0 ? '+' : ''}
                                {signal.contribution.toFixed(3)} to P(
                                {explanation.explanation.target_class})
                              </span>
                            </div>
                          </li>
                        )
                      )}
                    </ul>

                    <p className="mt-3 text-[10px] leading-4 text-slate-500">
                      SHAP signals describe model attribution, not causation.
                      Zero-valued temporal inputs may reflect unavailable
                      observations; they do not establish stability.
                    </p>
                  </>
                ) : (
                  <p
                    role="status"
                    className="mt-3 text-xs leading-5 text-slate-300"
                  >
                    The SHAP chart is available, but an AI risk summary could
                    not be produced: {explanation.risk_summary.error}
                  </p>
                )}
              </div>

              <p className="mt-5 flex items-center gap-2 border-t border-white/8 pt-4 text-[10px] text-slate-500">
                <Clock3 size={12} />
                Explanations support faculty review and are not a diagnosis.
              </p>
            </>
          )}

          {!explanation && !loadingExplanation && hasCurrentRisk && (
            <button
              onClick={() => void openTab('SHAP')}
              className="mt-5 inline-flex items-center gap-2 rounded-lg border border-cyan-200/20 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-200/5"
            >
              <Sparkles size={14} /> Calculate explanation
            </button>
          )}
        </section>
      )}
    </div>
  );
}

'use client';

import { ArrowRight, CalendarClock, CheckCircle2, CircleDashed } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Empty, ErrorNotice } from '@/components/ui';
import { api, readCachedApiData } from '@/lib/api';

type FocusSubject = {
  id: number;
  code: string;
  name: string;
  status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
  reason: string;
  latest_assessment: {
    assessment: string;
    score: number | null;
    maximum_marks: number | null;
    normalized_score: number | null;
    recorded_at: string | null;
  } | null;
  official_failure: boolean | null;
  support_triggers: ('LOW_SCORE' | 'OFFICIAL_FAIL')[];
};

type FocusResponse = {
  policy: { threshold_score: number; threshold_maximum: number; active_for_classification: boolean } | null;
  subjects: FocusSubject[];
};

type RecoveryTask = {
  id: number;
  subject_id: number | null;
  subject: string | null;
  title: string;
  status: string;
  progress_percent: number;
  scheduled_date: string | null;
  scheduled_at: string | null;
  deadline: string | null;
};

function taskDate(task: RecoveryTask): string | null {
  return task.scheduled_date ?? task.scheduled_at ?? task.deadline;
}

function formatDate(value: string): string {
  const parsed = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? 'Date unavailable' : parsed.toLocaleDateString();
}

export default function StudentRecoveryRoadmapCards() {
  const [focus, setFocus] = useState<FocusResponse | null>(() => readCachedApiData<FocusResponse>('/api/v1/students/me/subject-focus?first_internal_only=true'));
  const [tasks, setTasks] = useState<RecoveryTask[] | null>(() => readCachedApiData<RecoveryTask[]>('/api/v1/students/me/recovery-tasks'));
  const [focusError, setFocusError] = useState('');
  const [tasksError, setTasksError] = useState('');

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api<FocusResponse>('/api/v1/students/me/subject-focus?first_internal_only=true'),
      api<RecoveryTask[]>('/api/v1/students/me/recovery-tasks'),
    ]).then(([focusResult, tasksResult]) => {
      if (!active) return;
      if (focusResult.status === 'fulfilled') setFocus(focusResult.value);
      else setFocusError(focusResult.reason instanceof Error ? focusResult.reason.message : 'Subject evidence could not be loaded.');
      if (tasksResult.status === 'fulfilled') setTasks(tasksResult.value);
      else setTasksError(tasksResult.reason instanceof Error ? tasksResult.reason.message : 'Published recovery tasks could not be loaded.');
    });
    return () => { active = false; };
  }, []);

  const tasksBySubject = new Map<number, RecoveryTask[]>();
  for (const task of tasks ?? []) {
    if (task.subject_id == null) continue;
    const subjectTasks = tasksBySubject.get(task.subject_id) ?? [];
    subjectTasks.push(task);
    tasksBySubject.set(task.subject_id, subjectTasks);
  }

  const cards = (focus?.subjects ?? []).filter((subject) =>
    subject.support_triggers.length > 0 || (tasksBySubject.get(subject.id)?.length ?? 0) > 0,
  );

  return (
    <section aria-label="Subject recovery roadmaps" className="space-y-3">
      <div>
        <p className="eyebrow">Subject-first support</p>
        <h2 className="mt-1 text-base font-semibold text-white">Your recovery roadmaps</h2>
        <p className="mt-1 text-xs text-slate-500">Each card stays with its own verified subject evidence and published tasks.</p>
      </div>
      {focusError && <ErrorNotice message={focusError} />}
      {tasksError && <ErrorNotice message={tasksError} />}
      {!focus && !focusError && <div role="status" className="surface rounded-xl p-4 text-xs text-slate-400">Loading your subject support records…</div>}
      {focus && !focus.policy?.active_for_classification && <p role="status" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] p-3 text-xs text-amber-100">Marks-based eligibility is unavailable until the configured 25-mark support policy is active. Explicit official FAIL records remain a separate support trigger.</p>}
      {focus && tasks !== null && cards.length === 0 && <div className="surface rounded-xl p-4"><Empty title="No published subject roadmap yet" detail="A subject card appears when verified evidence triggers support or a teacher publishes tasks for that subject." /></div>}
      {focus && tasksError && cards.length === 0 && <p role="status" className="rounded-lg border border-amber-200/15 bg-amber-200/[.035] p-3 text-xs text-amber-100">Published task data is unavailable, so task-only subject roadmaps cannot be identified.</p>}
      {cards.length > 0 && <div className="grid gap-3 md:grid-cols-2">
        {cards.map((subject) => {
          const subjectTasks = (tasksBySubject.get(subject.id) ?? []).sort((left, right) => {
            const leftDate = taskDate(left);
            const rightDate = taskDate(right);
            if (!leftDate && !rightDate) return left.id - right.id;
            if (!leftDate) return 1;
            if (!rightDate) return -1;
            return new Date(leftDate).getTime() - new Date(rightDate).getTime();
          });
          const completed = subjectTasks.filter((task) => task.status === 'COMPLETED').length;
          const hasOfficialFail = subject.support_triggers.includes('OFFICIAL_FAIL');
          const isLow = subject.support_triggers.includes('LOW_SCORE');
          const eligible = isLow || hasOfficialFail;

          return <article key={subject.id} aria-label={`${subject.name} recovery roadmap`} className="surface relative overflow-hidden rounded-xl p-4">
            <div className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-rose-400/70 via-slate-300/30 to-rose-400/20" />
            <div className="flex items-start justify-between gap-3">
              <div><p className="eyebrow">{subject.code}</p><h3 className="mt-1 text-sm font-semibold text-white">{subject.name}</h3></div>
              <span className={`rounded-full border px-2.5 py-1 text-[9px] font-medium ${eligible ? 'border-rose-200/20 bg-rose-200/[.05] text-rose-100' : 'border-white/10 text-slate-300'}`}>
                {hasOfficialFail ? 'Official FAIL support' : isLow ? 'Below support threshold' : 'Teacher-published support'}
              </span>
            </div>
            <p className="mt-3 text-[10px] leading-5 text-slate-400">{subject.reason}</p>
            {subject.latest_assessment && <p className="mt-2 text-[10px] text-slate-500">
              {subject.latest_assessment.assessment}: {subject.latest_assessment.score ?? '—'} / {subject.latest_assessment.maximum_marks ?? '—'}
              {subject.latest_assessment.normalized_score != null ? ` · normalized ${subject.latest_assessment.normalized_score.toFixed(2)} / ${focus?.policy?.threshold_maximum ?? 25}` : ''}
            </p>}
            <div className="mt-4 rounded-lg border border-white/[.07] bg-black/[.10] p-3">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-slate-300">Published tasks</span>
                <span className="text-slate-400">{tasks ? `${completed} of ${subjectTasks.length} complete` : tasksError ? 'Unavailable' : 'Loading…'}</span>
              </div>
              {tasksError
                ? <p className="mt-2 text-[10px] text-amber-100">Published task details could not be loaded.</p>
                : tasks === null
                  ? <p role="status" className="mt-2 text-[10px] text-slate-500">Loading persisted roadmap tasks…</p>
                  : subjectTasks.length
                ? <ol className="relative mt-3 space-y-3 border-l border-rose-200/20 pl-4">
                  {subjectTasks.map((task) => {
                    const done = task.status === 'COMPLETED';
                    const scheduled = taskDate(task);
                    return <li key={task.id} className="relative">
                      <span className={`absolute -left-[21px] top-0.5 grid size-3 place-items-center rounded-full ${done ? 'bg-emerald-300/15 text-emerald-100' : 'bg-rose-300/15 text-rose-100'}`}>
                        {done ? <CheckCircle2 size={10} /> : <CircleDashed size={10} />}
                      </span>
                      <p className="text-[10px] font-medium text-slate-200">{task.title}</p>
                      <p className="mt-1 flex items-center gap-1 text-[9px] text-slate-500"><CalendarClock size={10} />{scheduled ? formatDate(scheduled) : 'Not scheduled'} · {task.status.replaceAll('_', ' ').toLowerCase()}</p>
                    </li>;
                  })}
                </ol>
                  : <p className="mt-2 text-[10px] text-slate-500">No published roadmap tasks are available for this subject yet.</p>}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link href={`/student/subjects/${subject.id}`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-[10px] text-slate-200 hover:border-rose-200/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
                Open subject workspace <ArrowRight size={12} />
              </Link>
              {subjectTasks.length > 0 && <Link href={`/student/recovery?subject_id=${subject.id}`} className="inline-flex min-h-9 items-center rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">View tasks</Link>}
            </div>
          </article>;
        })}
      </div>}
    </section>
  );
}

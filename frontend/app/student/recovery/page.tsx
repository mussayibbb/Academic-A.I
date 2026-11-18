'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, Check, Clock3, FileText, MessageSquareText, RotateCcw, Send, Sparkles } from 'lucide-react';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading } from '@/components/ui';
import StudentLearningRecoveryPanel from '@/components/StudentLearningRecoveryPanel';
import StudentRecoveryQuizzes from '@/components/StudentRecoveryQuizzes';
import StudentRecoveryRoadmapCards from '@/components/StudentRecoveryRoadmapCards';
import { api } from '@/lib/api';

type EvidenceRecord = {
  ref: string;
  kind: string;
  subject: string;
  academic_period?: string;
  values?: Record<string, { value: number; observed_at: string | null }>;
  assessment?: string;
  score?: number;
  maximum_marks?: number;
  recorded_at?: string;
  attended?: boolean;
  attendance_rate?: number | null;
  observed_at?: string;
};

type RecoveryTask = {
  id: number;
  subject_id: number | null;
  subject: string | null;
  topic: string | null;
  stage?: 'MODULE_3' | 'MODULE_4' | 'REVISION_ASSESSMENT' | null;
  title: string;
  learning_objective: string;
  instructions: string;
  activity_type: string;
  source: string;
  priority: string;
  scheduled_at: string | null;
  deadline: string | null;
  estimated_minutes: number;
  resources: { title: string; url: string | null }[];
  completion_criteria: string;
  status: 'AVAILABLE' | 'IN_PROGRESS' | 'SUBMITTED' | 'NEEDS_REVISION' | 'COMPLETED';
  progress_percent: number;
  student_response: string | null;
  submitted_at: string | null;
  teacher_feedback: string | null;
  assessed_score: number | null;
  revision_count: number;
  evidence_refs: string[];
  evidence: EvidenceRecord[];
  field_provenance: Record<string, string>;
  activity: {
    reason: string;
    lesson: string;
    steps: string[];
    exercise: string;
    submission_format: string;
  };
};
type TimelineItem = { kind: string; occurred_at: string; title: string; summary: unknown; status: string };
type RecoveryTutorResult = {
  status: 'answered' | 'unsupported' | 'no_evidence' | 'unavailable';
  answer_source: 'provider' | 'built_in' | 'deterministic_fallback';
  answer_scope: 'GENERAL_EXPLANATION' | 'STUDENT_EVIDENCE' | null;
  answer: string;
  practice_suggestions: string[];
  evidence_refs: string[];
  evidence: EvidenceRecord[];
  missing_information: string[];
  provider: string | null;
  document_retrieval: 'disabled_unscoped_index';
};
type TutorTurn = { role: 'user' | 'assistant'; content: string };

function evidenceDescription(item: EvidenceRecord): string {
  if (item.kind === 'assessment_mark') {
    return `${item.assessment ?? 'Assessment'}: ${item.score} / ${item.maximum_marks}`;
  }
  if (item.kind === 'student_subject_academic_record') {
    const values = Object.entries(item.values ?? {}).map(([name, record]) => `${name.replaceAll('_', ' ')} ${record.value}`);
    return `${item.academic_period ?? 'Academic record'} · ${values.join(' · ')}`;
  }
  if (item.kind === 'attendance_record') {
    const attendance = item.attendance_rate == null ? '' : ` · recorded rate ${item.attendance_rate}%`;
    return `${item.attended ? 'Attended' : 'Not attended'}${attendance}`;
  }
  return item.kind.replaceAll('_', ' ');
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : 'Not scheduled';
}

function workedExample(task: RecoveryTask): string {
  const assessment = task.evidence.find((item) => item.kind === 'assessment_mark');
  if (assessment) {
    return `The linked record reads “${assessment.assessment ?? 'Assessment'}: ${assessment.score} / ${assessment.maximum_marks}.” This is the recorded result for that assessment. By itself, it does not identify which topic or concept needs more practice.`;
  }
  const record = task.evidence[0];
  if (record) {
    return `Start with the linked ${record.kind.replaceAll('_', ' ')} for ${record.subject}. Describe only what the record directly states, then note what extra feedback you would need before drawing a conclusion.`;
  }
  return 'No academic record is linked to this task, so there is no verified example to interpret. Ask your teacher to connect the activity to an appropriate course record or material.';
}

function nextAction(status: RecoveryTask['status']): string {
  switch (status) {
    case 'AVAILABLE': return 'Start the learning activity';
    case 'IN_PROGRESS': return 'Continue your activity';
    case 'NEEDS_REVISION': return 'Review feedback and revise';
    case 'SUBMITTED': return 'Waiting for teacher review';
    case 'COMPLETED': return 'Review your teacher feedback';
  }
}

export default function StudentRecoveryPage() {
  const [tasks, setTasks] = useState<RecoveryTask[] | null>(null);
  const [subjectFilter, setSubjectFilter] = useState('');
  const [subjectIdFilter, setSubjectIdFilter] = useState('');
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [responses, setResponses] = useState<Record<number, string>>({});
  const [progress, setProgress] = useState<Record<number, number>>({});
  const [openTaskId, setOpenTaskId] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tutorQuestion, setTutorQuestion] = useState('');
  const [tutorResult, setTutorResult] = useState<RecoveryTutorResult | null>(null);
  const [tutorTurns, setTutorTurns] = useState<TutorTurn[]>([]);
  const [tutorBusy, setTutorBusy] = useState(false);
  const [tutorError, setTutorError] = useState('');
  const reduceMotion = useReducedMotion() ?? false;
  const visibleTasks = tasks?.filter((task) => (
    subjectIdFilter
      ? task.subject_id === Number(subjectIdFilter)
      : !subjectFilter || task.subject?.toLocaleLowerCase() === subjectFilter.toLocaleLowerCase()
  )) ?? [];
  const selectedSubjectName = subjectFilter || (subjectIdFilter
    ? tasks?.find((task) => task.subject_id === Number(subjectIdFilter))?.subject ?? ''
    : '');
  const completedTaskCount = tasks?.filter((task) => task.status === 'COMPLETED').length ?? 0;
  const inProgressTaskCount = tasks?.filter((task) => task.status === 'IN_PROGRESS').length ?? 0;
  const submittedTaskCount = tasks?.filter((task) => task.status === 'SUBMITTED').length ?? 0;
  const overallProgress = tasks?.length
    ? Math.round(tasks.reduce((total, task) => total + task.progress_percent, 0) / tasks.length)
    : 0;

  const reload = useCallback(async () => {
    const [items, activity] = await Promise.all([
      api<RecoveryTask[]>('/api/v1/students/me/recovery-tasks'),
      api<{ items: TimelineItem[] }>('/api/v1/students/me/recovery-timeline'),
    ]);
    setTasks(items);
    setTimeline(activity.items);
    setProgress(Object.fromEntries(items.map((item) => [item.id, item.progress_percent])));
  }, []);

  useEffect(() => {
    reload().catch((requestError: Error) => setError(requestError.message));
  }, [reload]);

  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    setSubjectFilter(search.get('subject') ?? '');
    setSubjectIdFilter(search.get('subject_id') ?? '');
  }, []);

  const updateProgress = async (task: RecoveryTask, action: 'start' | 'progress') => {
    setBusy(task.id);
    setError('');
    setNotice('');
    try {
      await api<RecoveryTask>(`/api/v1/recovery-tasks/${task.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          action,
          ...(action === 'progress' ? { progress_percent: progress[task.id] ?? task.progress_percent } : {}),
          idempotency_key: crypto.randomUUID(),
        }),
      });
      await reload();
      if (action === 'start') setOpenTaskId(task.id);
      setNotice(action === 'start' ? 'Task started.' : 'Your progress was saved.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = async (task: RecoveryTask) => {
    const response = responses[task.id] ?? task.student_response ?? '';
    setBusy(task.id);
    setError('');
    setNotice('');
    try {
      await api<RecoveryTask>(`/api/v1/recovery-tasks/${task.id}/draft`, {
        method: 'PUT',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), response }),
      });
      await reload();
      setNotice('Your work was saved as a draft. It has not been submitted for review.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>, task: RecoveryTask) => {
    event.preventDefault();
    const response = (responses[task.id] ?? task.student_response ?? '').trim();
    if (!response) {
      setError('Add your work or reflection before submitting.');
      return;
    }
    setBusy(task.id);
    setError('');
    setNotice('');
    try {
      await api<RecoveryTask>(`/api/v1/recovery-tasks/${task.id}/submit`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), response }),
      });
      await reload();
      setResponses((current) => ({ ...current, [task.id]: '' }));
      setNotice('Your recovery task was submitted for teacher review.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const askTutor = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = tutorQuestion.trim();
    if (!question) return;
    setTutorBusy(true);
    setTutorError('');
    setTutorResult(null);
    try {
      const result = await api<RecoveryTutorResult>('/api/v1/students/me/recovery-tutor', {
        method: 'POST',
        body: JSON.stringify({ question, conversation_history: tutorTurns.slice(-6) }),
      });
      setTutorResult(result);
      setTutorTurns((current) => {
        const nextTurns: TutorTurn[] = [...current, { role: 'user', content: question }, { role: 'assistant', content: result.answer }];
        return nextTurns.slice(-6);
      });
    } catch (requestError) {
      setTutorError((requestError as Error).message);
    } finally {
      setTutorBusy(false);
    }
  };

  if (tasks === null && !error) {
    return <div className="space-y-4" aria-label="Loading recovery tasks">
      <div className="skeleton h-28 rounded-xl" />
      <div className="skeleton h-52 rounded-xl" />
    </div>;
  }

  return <div className="fade-up space-y-5">
    <PageHeading
      eyebrow="Your learning space · teacher-published"
      title={selectedSubjectName ? `${selectedSubjectName} recovery tasks` : subjectIdFilter ? 'Subject recovery tasks' : 'Recovery tasks'}
      description={subjectFilter || subjectIdFilter ? 'Your published tasks and feedback for this subject.' : 'Work through tasks your teacher has published. Your progress and submissions are visible only to you and authorized teachers.'}
    />
    <StudentRecoveryQuizzes />
    {!subjectFilter && !subjectIdFilter && tasks && <section aria-label="Persisted recovery progress" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="Published tasks" value={tasks.length} detail="Persisted across all recovery subjects" color="rose" />
      <Metric label="Completed" value={completedTaskCount} detail="Teacher-assessed complete" color="mint" />
      <Metric label="In progress" value={inProgressTaskCount} detail="Saved student progress" color="amber" />
      <Metric label="Submitted" value={submittedTaskCount} detail="Awaiting teacher review" color="rose" />
      <div className="surface-soft rounded-xl p-4 sm:col-span-2 xl:col-span-4">
        <div className="flex items-center justify-between gap-3 text-xs"><span className="font-medium text-slate-200">Overall persisted progress</span><span className="font-semibold tabular-nums text-rose-100">{overallProgress}%</span></div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[.07]" role="progressbar" aria-label="Overall recovery task progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={overallProgress}>
          <div className="h-full rounded-full bg-rose-500 transition-[width]" style={{ width: `${overallProgress}%` }} />
        </div>
      </div>
    </section>}
    {!subjectFilter && !subjectIdFilter && <StudentRecoveryRoadmapCards />}
    {error && <div className="space-y-2"><ErrorNotice message={error} /><button type="button" onClick={() => { setError(''); void reload().catch((requestError: Error) => setError(requestError.message)); }} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/30"><RotateCcw size={13} /> Retry loading tasks</button></div>}
    {notice && <div role="status" className="rounded-lg border border-rose-200/20 bg-rose-200/[.04] px-4 py-3 text-xs text-rose-100">{notice}</div>}
    <section className="surface overflow-hidden rounded-xl border border-rose-300/15">
      <div className="border-l-2 border-rose-500/70 p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-rose-300/20 bg-rose-300/[.06] text-rose-200"><MessageSquareText size={17} /></span>
          <div>
            <p className="eyebrow">Evidence-grounded study support</p>
            <h2 className="mt-1 text-lg font-semibold text-white">Recovery AI tutor</h2>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">Ask for a general explanation, a hint, or a follow-up. Replies are labeled as general AI study support or explanations of linked records. No course documents are used, and the tutor cannot change grades, exams, or assignments.</p>
          </div>
        </div>
        {tutorTurns.length > (tutorResult ? 2 : 0) && <details className="mt-4 rounded-lg border border-white/[.07] p-3">
          <summary className="cursor-pointer text-xs font-medium text-slate-300">Recent tutor conversation ({tutorResult ? tutorTurns.length - 2 : tutorTurns.length} messages)</summary>
          <ol className="mt-3 space-y-2">{(tutorResult ? tutorTurns.slice(0, -2) : tutorTurns).map((turn, index) => <li key={`${index}-${turn.role}`} className="text-xs leading-5">
            <span className="font-medium text-rose-200">{turn.role === 'user' ? 'You' : 'Tutor'}:</span>{' '}
            <span className="whitespace-pre-wrap text-slate-300">{turn.content}</span>
          </li>)}</ol>
        </details>}
        <form onSubmit={(event) => void askTutor(event)} className="mt-4">
          <label htmlFor="recovery-tutor-question" className="text-xs font-medium text-slate-200">Your question</label>
          <textarea
            id="recovery-tutor-question"
            rows={3}
            maxLength={2000}
            value={tutorQuestion}
            onChange={(event) => setTutorQuestion(event.target.value)}
            className="form-input resize-y"
            placeholder="Ask for a concept explanation, a hint, or a follow-up question."
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" onClick={() => setTutorQuestion('Give me a hint, one step at a time, for how to approach this learning activity.')} className="min-h-9 rounded-lg border border-white/10 px-3 text-[10px] text-slate-300 hover:border-rose-300/25">Give me a hint</button>
            <button type="button" onClick={() => setTutorQuestion('Explain the key idea in this activity in simpler terms.')} className="min-h-9 rounded-lg border border-white/10 px-3 text-[10px] text-slate-300 hover:border-rose-300/25">Simplify an explanation</button>
            <button type="button" onClick={() => setTutorQuestion('Can you explain your previous answer with a different example?')} disabled={!tutorTurns.length} className="min-h-9 rounded-lg border border-white/10 px-3 text-[10px] text-slate-300 hover:border-rose-300/25 disabled:cursor-not-allowed disabled:opacity-50">Ask a follow-up</button>
          </div>
          <button type="submit" disabled={tutorBusy || !tutorQuestion.trim()} className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50">
            {tutorBusy ? <><Sparkles size={14} className="animate-pulse" />Thinking…</> : <><Send size={13} />Ask tutor</>}
          </button>
        </form>
        {tutorError && <div className="mt-4"><ErrorNotice message={tutorError} /></div>}
        {tutorResult && <div role="status" aria-live="polite" className="mt-4 rounded-lg border border-white/[.08] bg-black/10 p-4">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-rose-200">
            {tutorResult.answer_source === 'provider'
              ? `${tutorResult.answer_scope === 'STUDENT_EVIDENCE' ? 'AI explanation of verified academic records' : 'AI-generated general explanation · not verified course material'}${tutorResult.provider ? ` · ${tutorResult.provider}` : ''}`
              : tutorResult.answer_source === 'built_in'
                ? 'Built-in greeting'
                : 'Deterministic guidance · no AI answer generated'}
          </p>
          <p className={`text-sm leading-6 ${tutorResult.status === 'answered' ? 'text-slate-200' : 'text-rose-100'}`}>{tutorResult.answer}</p>
          {tutorResult.status === 'no_evidence' && <p className="mt-2 text-xs text-slate-400">No verified academic evidence was available for this question.</p>}
          {tutorResult.practice_suggestions.length > 0 && <div className="mt-3">
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-rose-200">Optional practice ideas</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5 text-slate-300">{tutorResult.practice_suggestions.map((suggestion, index) => <li key={`${index}-${suggestion}`}>{suggestion}</li>)}</ul>
          </div>}
          {tutorResult.evidence.length > 0 && <div className="mt-3 border-t border-white/[.07] pt-3">
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Verified evidence cited</h3>
            <ul className="mt-2 space-y-2">{tutorResult.evidence.map((item) => <li key={item.ref} className="flex flex-wrap justify-between gap-x-3 text-xs">
              <span className="text-slate-200">{item.subject}</span><span className="text-slate-400">{evidenceDescription(item)}</span>
            </li>)}</ul>
          </div>}
          {tutorResult.answer_source === 'provider' && <p className="mt-3 text-[10px] text-slate-600">AI explanations are study support, not official academic decisions.</p>}
          <p className="mt-2 text-[10px] leading-4 text-slate-600">Course documents were not used because the current shared index cannot enforce course-level access.</p>
        </div>}
      </div>
    </section>
    {visibleTasks.length ? <div className="grid gap-4">
      {visibleTasks.map((task, index) => (
        <motion.article
          key={task.id}
          initial={reduceMotion ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.28, delay: reduceMotion ? 0 : index * 0.04 }}
          className="surface overflow-hidden rounded-xl"
        >
          <div className="border-l-2 border-rose-500/70 p-4 sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="eyebrow">{task.subject ?? 'Recovery roadmap'}{task.topic ? ` · ${task.topic}` : ''}</div>
                <h2 className="mt-1 text-lg font-semibold text-white">{task.title}</h2>
                {task.stage && <p className="mt-1 text-[10px] font-medium text-rose-200">{task.stage === 'MODULE_3' ? 'Stage 1 · Module 3' : task.stage === 'MODULE_4' ? 'Stage 2 · Module 4' : 'Stage 3 · Revision and assessment practice'}</p>}
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300"><span className="font-medium text-slate-100">Goal:</span> {task.learning_objective}</p>
              </div>
              <span className={`rounded-full border px-3 py-1 text-[10px] font-semibold tracking-wide ${task.status === 'COMPLETED' ? 'border-white/15 bg-white/5 text-white' : task.status === 'NEEDS_REVISION' ? 'border-rose-300/25 bg-rose-300/10 text-rose-100' : 'border-white/10 bg-white/[.03] text-slate-300'}`}>
                {task.status.replaceAll('_', ' ')}
              </span>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className="inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-white/[.08] bg-black/10 px-3 text-[10px] text-slate-300"><Clock3 size={12} />{task.estimated_minutes} min{task.field_provenance.estimated_minutes?.startsWith('PROPOSED') ? ' · estimate' : ''}</span>
              <span className="rounded-lg border border-white/[.08] bg-black/10 px-3 py-2 text-[10px] text-slate-300">{nextAction(task.status)}</span>
              {task.scheduled_at && <span className="rounded-lg border border-white/[.08] bg-black/10 px-3 py-2 text-[10px] text-slate-400">Scheduled · {formatDate(task.scheduled_at)}</span>}
              {task.deadline && <span className="rounded-lg border border-white/[.08] bg-black/10 px-3 py-2 text-[10px] text-slate-400">Deadline · {formatDate(task.deadline)}</span>}
            </div>

            <details className="mt-3 rounded-lg border border-white/[.07] bg-white/[.02] p-3">
              <summary className="cursor-pointer text-xs font-medium text-slate-300">Task rationale and instructions</summary>
              <p className="mt-3 whitespace-pre-wrap text-xs leading-5 text-slate-400">{task.instructions}</p>
              <p className="mt-2 text-xs text-slate-500">Complete when: {task.completion_criteria}</p>
              <p className="mt-3 whitespace-pre-wrap text-xs leading-5 text-slate-400"><span className="font-medium text-slate-300">Why assigned:</span> {task.activity.reason}</p>
            </details>

            {task.status !== 'AVAILABLE' || openTaskId === task.id ? <section aria-label={`${task.title} learning activity`} className="mt-4 space-y-4 rounded-lg border border-rose-300/15 bg-rose-300/[.025] p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div><p className="eyebrow">Learning activity</p><h3 className="mt-1 text-sm font-semibold text-white">{task.subject ?? 'Study skills'} practice</h3></div>
                <span className="rounded-full border border-white/10 px-2.5 py-1 text-[9px] text-slate-400">
                  {task.field_provenance.activity === 'TEACHER_AUTHORED_APPROVED' || task.source === 'TEACHER_AUTHORED'
                    ? 'Teacher-reviewed activity'
                    : task.resources.length
                      ? 'Teacher-provided resources · check relevance'
                      : 'General evidence-study skill · not a verified course lesson'}
                </span>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-rose-100">Objective</h3>
                <p className="mt-1 text-xs leading-5 text-slate-300">{task.learning_objective}</p>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-200">Explanation</h3>
                <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.activity.lesson}</p>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-200">Worked example using the linked record</h3>
                <p className="mt-1 text-xs leading-5 text-slate-300">{workedExample(task)}</p>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-200">Practice steps</h3>
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs leading-5 text-slate-300">{task.activity.steps.map((step, stepIndex) => <li key={`${stepIndex}-${step}`}>{step}</li>)}</ol>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-200">Practice activity</h3>
                <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.activity.exercise}</p>
              </div>
              <div>
                <h3 className="text-xs font-semibold text-slate-200">What to submit</h3>
                <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.activity.submission_format}</p>
              </div>
            </section> : null}

            {task.evidence.length > 0 ? <details className="mt-4 rounded-lg border border-white/[.07] bg-white/[.02] p-3">
              <summary className="cursor-pointer text-xs font-medium text-slate-200">Academic evidence linked to this task ({task.evidence.length})</summary>
              <ul className="mt-3 space-y-2">
                {task.evidence.map((item) => <li key={item.ref} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-white/[.06] pt-2 text-xs first:border-0 first:pt-0">
                  <span className="text-slate-200">{item.subject}</span>
                  <span className="text-slate-400">{evidenceDescription(item)}</span>
                  <span className="w-full text-[10px] text-slate-600">{item.recorded_at ?? item.observed_at ?? ''}</span>
                </li>)}
              </ul>
              <p className="mt-3 text-[10px] leading-5 text-slate-500">Recorded marks are not a claim about mastery of any specific topic. Review the evidence with your teacher.</p>
            </details> : <p className="mt-4 rounded-lg border border-white/[.07] px-3 py-2 text-[11px] text-slate-500">No academic evidence was linked to this task.</p>}

            {task.resources.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{task.resources.map((resource) => resource.url
              ? <a key={`${resource.title}-${resource.url}`} href={resource.url} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-300 hover:border-rose-300/25"><FileText size={13} />{resource.title}<ArrowRight size={12} /></a>
              : <span key={resource.title} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-300"><FileText size={13} />{resource.title}</span>)}</div>}

            {task.status === 'IN_PROGRESS' && <div className="mt-4 rounded-lg border border-white/[.07] p-4">
              <label className="flex items-center justify-between text-xs text-slate-300" htmlFor={`progress-${task.id}`}>
                <span>Progress</span><span>{progress[task.id] ?? task.progress_percent}%</span>
              </label>
              <input id={`progress-${task.id}`} type="range" min="0" max="99" value={progress[task.id] ?? task.progress_percent} onChange={(event) => setProgress((current) => ({ ...current, [task.id]: Number(event.target.value) }))} className="mt-3 w-full accent-rose-500" />
              <button type="button" disabled={busy === task.id} onClick={() => void updateProgress(task, 'progress')} className="mt-3 min-h-10 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50">Save progress</button>
            </div>}

            {task.status === 'IN_PROGRESS' || task.status === 'NEEDS_REVISION' ? <form onSubmit={(event) => void submit(event, task)} className="mt-4 rounded-lg border border-rose-300/15 bg-rose-300/[.025] p-4">
              <label htmlFor={`response-${task.id}`} className="text-xs font-medium text-slate-200">{task.status === 'NEEDS_REVISION' ? 'Revise your response' : 'Your work or reflection'}</label>
              <textarea id={`response-${task.id}`} rows={6} maxLength={20000} value={responses[task.id] ?? task.student_response ?? ''} onChange={(event) => setResponses((current) => ({ ...current, [task.id]: event.target.value }))} className="form-input resize-y" placeholder="Write your response or completed work. Save a draft before submitting if you need to return later." />
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" disabled={busy === task.id} onClick={() => void saveDraft(task)} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-4 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50">{busy === task.id ? 'Saving…' : 'Save draft'}</button>
                <button type="submit" disabled={busy === task.id} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50">{busy === task.id ? 'Submitting…' : task.status === 'NEEDS_REVISION' ? 'Submit revision' : 'Submit for review'}<ArrowRight size={13} /></button>
              </div>
            </form> : null}

            {task.status === 'AVAILABLE' && <button type="button" disabled={busy === task.id} onClick={() => void updateProgress(task, 'start')} className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50"><Sparkles size={14} />{busy === task.id ? 'Starting…' : 'Start task'}<ArrowRight size={13} /></button>}
            {task.status === 'SUBMITTED' && <div className="mt-4 rounded-lg border border-white/[.07] bg-white/[.02] p-4"><p className="flex items-center gap-2 text-xs font-medium text-slate-200"><Check size={14} className="text-rose-200" />Submitted for teacher review</p>{task.student_response && <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-400">{task.student_response}</p>}</div>}
            {task.teacher_feedback && <div role="status" className="mt-4 rounded-lg border border-rose-300/15 bg-rose-300/[.035] p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-rose-200">Teacher feedback{task.assessed_score == null ? '' : ` · ${task.assessed_score}`}</p><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.teacher_feedback}</p></div>}

            <details className="mt-4 text-[10px] text-slate-600">
              <summary className="cursor-pointer">Task field provenance</summary>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">{Object.entries(task.field_provenance).map(([field, source]) => <li key={field}><span className="text-slate-400">{field.replaceAll('_', ' ')}:</span> {source.replaceAll('_', ' ').toLowerCase()}</li>)}</ul>
            </details>
          </div>
        </motion.article>
      ))}
    </div> : tasks !== null ? <section className="surface rounded-xl p-6"><Empty title={subjectFilter || subjectIdFilter ? 'No tasks for this subject yet' : 'No recovery tasks published yet'} detail="Published tasks and module assignments appear here after your teacher approves them." /></section> : null}
    <StudentLearningRecoveryPanel
      subjectId={subjectIdFilter ? Number(subjectIdFilter) : undefined}
      subjectName={!subjectIdFilter ? subjectFilter || undefined : undefined}
    />
    <section className="surface rounded-xl p-4 sm:p-5">
      <div className="mb-3 flex items-center gap-2"><Clock3 size={15} className="text-rose-200" /><h2 className="text-sm font-semibold text-slate-100">Recovery activity</h2></div>
      {timeline.length ? <ol className="space-y-3">{timeline.slice(0, 8).map((item, index) => <li key={`${item.kind}-${item.occurred_at}-${index}`} className="flex gap-3 border-l border-rose-500/25 pl-3">
        <span className="mt-1 size-1.5 shrink-0 rounded-full bg-rose-400" />
        <div className="min-w-0"><p className="text-xs font-medium text-slate-200">{item.title}</p><p className="mt-1 text-[10px] text-slate-500">{new Date(item.occurred_at).toLocaleString()} · {item.status.replaceAll('_', ' ')}</p></div>
      </li>)}</ol> : <p className="text-xs text-slate-500">Published roadmap and task activity will appear here.</p>}
    </section>
  </div>;
}

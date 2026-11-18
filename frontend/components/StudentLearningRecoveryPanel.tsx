'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { AlertCircle, Check, Clock3, Download, LoaderCircle, Play, RotateCcw, Send } from 'lucide-react';
import { useEffect, useState } from 'react';
import { API_BASE, api, readToken } from '@/lib/api';
import { ErrorNotice } from '@/components/ui';

type Question = { id: number; question_type: 'MCQ' | 'SHORT_ANSWER'; question: string; options: string[]; marks: number; learning_objective: string; rubric: Record<string, unknown>; source_evidence: { ref: string; quote: string }[] };
type Resource = { id: number; title: string; original_filename: string; file_type: string; file_size: number };
type Timer = { id: number; started_at: string; required_duration_minutes: number; elapsed_seconds: number; remaining_seconds: number; completed_at: string | null; ready: boolean };
type Attempt = { id: number; status: string; percentage: number | null; total_marks: number | null; review_pending: boolean; question_set: { questions: Question[] } | null; responses?: { question_id: number; marks_awarded: number; feedback: string; review_pending: boolean }[] };
type LearningAssignment = {
  id: number; subject_id: number; subject_name: string; module_id: number; module_number: number | null; module_title: string;
  approved_topic_id: number; topic_title: string; topic_summary: string; title: string; learning_objective: string;
  lesson_content: string; expected_duration_minutes: number; status: string; study_session: Timer | null;
  assessment: { id: number; version: number; status: 'PUBLISHED'; question_count: number; questions: Question[] } | null;
  latest_attempt: Attempt | null; attempt_count: number; resources: Resource[];
};

export default function StudentLearningRecoveryPanel({ subjectId, subjectName }: { subjectId?: number; subjectName?: string }) {
  const [items, setItems] = useState<LearningAssignment[] | null>(null);
  const [timers, setTimers] = useState<Record<number, Timer>>({});
  const [timerSyncedAt, setTimerSyncedAt] = useState<Record<number, number>>({});
  const [clockTick, setClockTick] = useState(Date.now());
  const [attempts, setAttempts] = useState<Record<number, Attempt>>({});
  const [answers, setAnswers] = useState<Record<number, Record<number, string>>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const reduceMotion = useReducedMotion() ?? false;

  const reload = async () => {
    const result = await api<LearningAssignment[]>('/api/v1/students/me/topic-learning-assignments');
    setItems(result);
    setTimers(Object.fromEntries(result.filter((item) => item.study_session).map((item) => [item.id, item.study_session as Timer])));
    setTimerSyncedAt(Object.fromEntries(result.filter((item) => item.study_session).map((item) => [item.id, Date.now()])));
    setAttempts((current) => ({
      ...current,
      ...Object.fromEntries(result.filter((item) => item.latest_attempt?.status === 'IN_PROGRESS').map((item) => [item.id, item.latest_attempt as Attempt])),
    }));
  };

  useEffect(() => { void reload().catch((requestError: Error) => setError(requestError.message)); }, []);

  const visibleItems = items?.filter((item) => (
    (subjectId === undefined || item.subject_id === subjectId)
    && (subjectName === undefined || item.subject_name.toLocaleLowerCase() === subjectName.toLocaleLowerCase())
  )) ?? [];

  useEffect(() => {
    if (!items?.some((item) => timers[item.id] && !timers[item.id].completed_at)) return;
    const timer = window.setInterval(() => setClockTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [items, timers]);

  const act = async (assignmentId: number, action: () => Promise<void>, success: string) => {
    setBusy(assignmentId);
    setError('');
    setNotice('');
    try { await action(); await reload(); setNotice(success); }
    catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(null); }
  };

  const startTimer = (item: LearningAssignment) => act(item.id, async () => {
    const timer = await api<Timer>(`/api/v1/topic-learning-assignments/${item.id}/study-session`, { method: 'POST' });
    setTimers((current) => ({ ...current, [item.id]: timer }));
    setTimerSyncedAt((current) => ({ ...current, [item.id]: Date.now() }));
  }, 'Your timer is saved on the server and resumes if you leave this page.');

  const verifyTimer = (item: LearningAssignment) => act(item.id, async () => {
    const timer = await api<Timer>(`/api/v1/topic-learning-assignments/${item.id}/study-session/complete`, { method: 'POST' });
    setTimers((current) => ({ ...current, [item.id]: timer }));
    setTimerSyncedAt((current) => ({ ...current, [item.id]: Date.now() }));
  }, 'Required elapsed time was verified by the server.');

  const startAttempt = (item: LearningAssignment) => act(item.id, async () => {
    const attempt = await api<Attempt & { question_set: { questions: Question[] } }>(`/api/v1/topic-learning-assignments/${item.id}/attempts`, { method: 'POST' });
    setAttempts((current) => ({ ...current, [item.id]: attempt }));
    setAnswers((current) => ({ ...current, [item.id]: {} }));
  }, 'Assessment started. Your answers remain available if you refresh.');

  const submitAttempt = (item: LearningAssignment) => {
    const attempt = attempts[item.id] ?? item.latest_attempt;
    if (!attempt) return;
    const questions = attempt.question_set?.questions ?? [];
    if (questions.some((question) => !(answers[item.id]?.[question.id] ?? '').trim())) {
      setError('Answer all 20 questions before submitting.');
      return;
    }
    void act(item.id, async () => {
      await api(`/api/v1/assessment-attempts/${attempt.id}/submit`, {
        method: 'POST', body: JSON.stringify({ answers: answers[item.id] }),
      });
      setAttempts((current) => { const next = { ...current }; delete next[item.id]; return next; });
      setAnswers((current) => { const next = { ...current }; delete next[item.id]; return next; });
    }, 'Your assessment was submitted. Any uncertain short answers are held for teacher review.');
  };

  const downloadResource = async (item: LearningAssignment, resource: Resource) => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/topic-learning-assignments/${item.id}/resources/${resource.id}`, {
        headers: { Authorization: `Bearer ${readToken() ?? ''}` }, cache: 'no-store',
      });
      if (!response.ok) throw new Error(`Resource download failed (${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = resource.original_filename; anchor.click(); URL.revokeObjectURL(url);
    } catch (requestError) { setError((requestError as Error).message); }
  };

  if (items === null && !error) return <section className="surface rounded-xl p-4" role="status"><div className="flex items-center gap-2 text-xs text-slate-400"><LoaderCircle size={14} className="animate-spin" />Loading subject recovery activities…</div></section>;

  return <section className="space-y-4" aria-label="Subject module recovery activities">
    <div className="flex flex-wrap items-end justify-between gap-2"><div><p className="eyebrow">Subject recovery · teacher-assigned</p><h2 className="mt-1 text-base font-semibold text-white">Module learning and assessments</h2></div><span className="text-[10px] text-slate-500">Progress is persisted per subject and topic</span></div>
    {error && <ErrorNotice message={error} />}
    {notice && <div role="status" aria-live="polite" className="rounded-lg border border-emerald-200/15 bg-emerald-200/[.035] px-3 py-2 text-xs text-emerald-100">{notice}</div>}
    {visibleItems.length ? visibleItems.map((item, index) => {
      const timer = timers[item.id] ?? item.study_session;
      const elapsedSinceSync = timerSyncedAt[item.id] === undefined ? 0 : Math.floor((clockTick - timerSyncedAt[item.id]) / 1000);
      const remaining = Math.max(0, (timer?.remaining_seconds ?? ((timer?.required_duration_minutes ?? item.expected_duration_minutes) * 60 - (timer?.elapsed_seconds ?? 0))) - elapsedSinceSync);
      const attempt = attempts[item.id] ?? item.latest_attempt;
      const activeQuestions = attempt?.status === 'IN_PROGRESS' ? attempt.question_set?.questions ?? [] : [];
      return <motion.article key={item.id} initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2, delay: reduceMotion ? 0 : index * 0.04 }} className="surface overflow-hidden rounded-xl">
        <div className="border-l-2 border-rose-500/70 p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><p className="eyebrow">{item.subject_name} · Module {item.module_number ?? '—'}</p><h3 className="mt-1 text-sm font-semibold text-white">{item.topic_title}</h3><p className="mt-1 text-xs leading-5 text-slate-400">{item.learning_objective}</p></div><span className={`rounded-full border px-2.5 py-1 text-[9px] font-semibold ${item.status === 'MASTERED' ? 'border-emerald-300/20 text-emerald-100' : item.status === 'REVISION_REQUIRED' ? 'border-amber-300/20 text-amber-100' : 'border-white/10 text-slate-300'}`}>{item.status.replaceAll('_', ' ')}</span></div>
          <details className="mt-3 rounded-md border border-white/[.07] p-3"><summary className="cursor-pointer text-[10px] font-medium text-slate-300">Lesson and source context</summary><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-400">{item.lesson_content}</p><p className="mt-2 text-[10px] leading-5 text-slate-500">{item.topic_summary}</p></details>
          {!!item.resources.length && <div className="mt-3 flex flex-wrap gap-2">{item.resources.map((resource) => <button key={resource.id} type="button" onClick={() => void downloadResource(item, resource)} className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-white/10 px-2.5 text-[10px] text-slate-300 hover:border-rose-300/20"><Download size={11} />{resource.original_filename}</button>)}</div>}

          <div className="mt-4 rounded-lg border border-white/[.08] bg-black/[.12] p-3">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="flex items-center gap-1.5 text-[10px] font-semibold text-slate-200"><Clock3 size={12} />Required study duration · {item.expected_duration_minutes} minutes</p><p className="mt-1 text-[9px] text-slate-500">The server verifies elapsed time, not attention or comprehension.</p></div>
              {!timer ? <button type="button" disabled={busy !== null} onClick={() => void startTimer(item)} className="inline-flex min-h-8 items-center gap-1.5 rounded-md bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:opacity-50"><Play size={11} />{busy === item.id ? 'Starting…' : 'Start study timer'}</button>
                : <div className="text-right"><div aria-live="polite" className="font-mono text-base font-semibold text-slate-100">{timer.completed_at ? 'Verified' : `${Math.floor(remaining / 60).toString().padStart(2, '0')}:${(remaining % 60).toString().padStart(2, '0')}`}</div>{!timer.completed_at && remaining <= 0 && <button type="button" disabled={busy !== null} onClick={() => void verifyTimer(item)} className="mt-1 min-h-7 rounded border border-emerald-300/20 px-2 text-[9px] text-emerald-100 disabled:opacity-50">Verify elapsed time</button>}</div>}
            </div>
            {timer && <progress aria-label={`Study duration progress for ${item.topic_title}`} value={Math.min(100, Math.round(timer.elapsed_seconds / (item.expected_duration_minutes * 60) * 100))} max={100} className="mt-3 h-1.5 w-full accent-rose-400" />}
          </div>

          {attempt && attempt.status !== 'IN_PROGRESS' && <div role="status" className="mt-3 rounded-lg border border-white/[.08] p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-medium text-slate-200">Attempt {item.attempt_count} · {attempt.status.replaceAll('_', ' ')}</p>{attempt.percentage !== null && <span className="text-sm font-semibold text-white">{attempt.percentage}%</span>}</div><p className="mt-1 text-[10px] text-slate-400">{attempt.review_pending ? 'Short-answer marks are awaiting teacher review; mastery has not advanced.' : attempt.percentage !== null && attempt.percentage >= 80 ? 'Mastery threshold reached. Your teacher will make the next topic available.' : 'Revision required. Your topic remains current; ask your teacher for a fresh assessment when available.'}</p>{attempt.responses?.map((response) => <p key={response.question_id} className="mt-1 text-[9px] text-slate-500">Q{response.question_id}: {response.marks_awarded} mark · {response.feedback}</p>)}</div>}

          {!attempt?.question_set && item.assessment?.status === 'PUBLISHED' && item.status !== 'MASTERED' && <button type="button" disabled={busy !== null || !timer?.completed_at} onClick={() => void startAttempt(item)} className="mt-3 inline-flex min-h-9 items-center gap-2 rounded-md bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45"><Check size={12} />{busy === item.id ? 'Preparing…' : `Start published assessment · ${item.assessment.question_count} questions`}</button>}
          {!item.assessment && <p className="mt-3 rounded-md border border-amber-300/10 px-3 py-2 text-[10px] text-slate-500">Assessment is not available yet. Your teacher must review and publish it.</p>}

          {!!activeQuestions.length && <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); submitAttempt(item); }}>
            <div className="flex items-center justify-between"><h4 className="text-xs font-semibold text-white">Assessment</h4><span className="text-[9px] text-slate-500">{activeQuestions.length} questions · {activeQuestions.reduce((total, question) => total + question.marks, 0)} marks total</span></div>
            {activeQuestions.map((question, questionIndex) => <fieldset key={question.id} className="rounded-md border border-white/[.07] p-3"><legend className="px-1 text-[10px] font-medium text-slate-200">{questionIndex + 1}. {question.question}</legend><p className="mb-2 text-[9px] text-slate-600">{question.question_type === 'MCQ' ? 'Multiple choice' : 'Short answer'} · {question.marks} marks</p>
              {question.question_type === 'MCQ' ? <div className="grid gap-2 sm:grid-cols-2">{question.options.map((option, optionIndex) => { const choice = String.fromCharCode(65 + optionIndex); const selected = answers[item.id]?.[question.id] === choice; return <label key={choice} className={`flex cursor-pointer items-start gap-2 rounded border px-2.5 py-2 text-[10px] transition ${selected ? 'border-rose-300/40 bg-rose-300/[.07] text-white' : 'border-white/[.07] text-slate-300 hover:border-white/20'}`}><input type="radio" name={`question-${item.id}-${question.id}`} value={choice} checked={selected} onChange={() => setAnswers((current) => ({ ...current, [item.id]: { ...current[item.id], [question.id]: choice } }))} className="accent-rose-400" />{choice}. {option}</label>; })}</div>
                : <label className="block text-[10px] text-slate-400" htmlFor={`answer-${item.id}-${question.id}`}>Short answer<textarea id={`answer-${item.id}-${question.id}`} rows={2} maxLength={10000} value={answers[item.id]?.[question.id] ?? ''} onChange={(event) => setAnswers((current) => ({ ...current, [item.id]: { ...current[item.id], [question.id]: event.target.value } }))} placeholder="Answer in one or two lines." className="form-input resize-y" /></label>}
            </fieldset>)}
            <button type="submit" disabled={busy !== null} className="inline-flex min-h-9 items-center gap-2 rounded-md bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:opacity-50"><Send size={12} />{busy === item.id ? 'Submitting…' : 'Submit assessment'}</button>
          </form>}
          {item.status === 'REVISION_REQUIRED' && <div className="mt-3 flex items-start gap-2 rounded border border-amber-300/15 px-3 py-2 text-[10px] text-amber-100"><RotateCcw size={12} className="mt-0.5 shrink-0" />Your score is below 80%. You remain on this topic until a reviewed revision assessment is published.</div>}
          {item.status === 'MASTERED' && <div className="mt-3 flex items-center gap-2 rounded border border-emerald-300/15 px-3 py-2 text-[10px] text-emerald-100"><Check size={12} />Topic mastered at 80% or higher.</div>}
          {busy === item.id && <div className="sr-only" role="status"><AlertCircle size={12} />Updating learning activity…</div>}
        </div>
      </motion.article>;
    }) : <section className="surface rounded-xl p-5"><p className="text-xs text-slate-400">{subjectId === undefined && subjectName === undefined ? 'No module-based recovery activities have been assigned. Your existing Recovery Roadmap tasks appear above.' : 'No module-based activities have been assigned for this subject yet.'}</p></section>}
  </section>;
}
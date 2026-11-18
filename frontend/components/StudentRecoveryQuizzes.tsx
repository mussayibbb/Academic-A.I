'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

type QuizQuestion = {
  question: string;
  question_type: 'MCQ' | 'SHORT_ANSWER';
  options: string[];
  marks: number;
};
type RecoveryQuiz = {
  task_id: number;
  plan_id: number;
  subject: string;
  topic: string | null;
  day_no: number;
  scheduled_date: string | null;
  title: string;
  max_score: number;
  questions: QuizQuestion[];
};
type QuizResult = {
  score: number;
  max_score: number;
  percent: number;
  results: {
    question: string;
    answer: string;
    correct: boolean;
    correct_answer: string;
    explanation: string;
    score: number;
    max_score: number;
  }[];
};
type QuizAnswerMap = Record<number, Record<string, string>>;

export default function StudentRecoveryQuizzes() {
  const [quizzes, setQuizzes] = useState<RecoveryQuiz[] | null>(null);
  const [answers, setAnswers] = useState<QuizAnswerMap>({});
  const [results, setResults] = useState<Record<number, QuizResult>>({});
  const [loadingError, setLoadingError] = useState('');
  const [submissionError, setSubmissionError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState<number | null>(null);

  const loadQuizzes = useCallback(async () => {
    const response = await api<{ date: string; items: RecoveryQuiz[] }>('/api/v1/students/me/recovery-quizzes/today');
    setQuizzes(response.items);
    setLoadingError('');
  }, []);

  useEffect(() => {
    loadQuizzes().catch((error: Error) => setLoadingError(error.message));
  }, [loadQuizzes]);

  const updateAnswer = (taskId: number, questionIndex: number, answer: string) => {
    setAnswers((current) => ({
      ...current,
      [taskId]: { ...current[taskId], [String(questionIndex)]: answer },
    }));
  };

  const submitQuiz = async (event: FormEvent<HTMLFormElement>, quiz: RecoveryQuiz) => {
    event.preventDefault();
    const quizAnswers = answers[quiz.task_id] ?? {};
    if (Object.keys(quizAnswers).length !== quiz.questions.length
      || quiz.questions.some((_, index) => !quizAnswers[String(index)]?.trim())) {
      setSubmissionError('Answer each question before submitting the quiz.');
      return;
    }
    setBusy(quiz.task_id);
    setSubmissionError('');
    setNotice('');
    try {
      const result = await api<QuizResult>(
        `/api/v1/students/me/recovery-quizzes/${quiz.task_id}/attempts`,
        { method: 'POST', body: JSON.stringify({ answers: quizAnswers }) },
      );
      setResults((current) => ({ ...current, [quiz.task_id]: result }));
      setQuizzes((current) => (current ?? []).filter((item) => item.task_id !== quiz.task_id));
      setNotice(`Quiz graded: ${result.score}/${result.max_score} (${result.percent}%).`);
    } catch (error) {
      setSubmissionError(error instanceof Error ? error.message : 'Your quiz could not be submitted.');
    } finally {
      setBusy(null);
    }
  };

  return <section className="surface rounded-xl p-4 sm:p-5" aria-labelledby="student-recovery-quizzes-title">
    <p className="eyebrow">Daily subject practice</p>
    <h2 id="student-recovery-quizzes-title" className="mt-1 text-lg font-semibold text-white">Today’s recovery quizzes</h2>
    <p className="mt-1 text-xs leading-5 text-slate-400">Quizzes are based on modules selected by your teacher. Your answers are graded automatically and visible to you and your teacher.</p>
    {loadingError && <div role="alert" className="mt-3 rounded-lg border border-rose-300/25 bg-rose-300/[.06] p-3 text-xs text-rose-100">
      {loadingError}
      <button type="button" className="ml-3 underline" onClick={() => void loadQuizzes().catch((error: Error) => setLoadingError(error.message))}>Retry</button>
    </div>}
    {submissionError && <div role="alert" className="mt-3 rounded-lg border border-rose-300/25 bg-rose-300/[.06] p-3 text-xs text-rose-100">{submissionError}</div>}
    {notice && <p role="status" aria-live="polite" className="mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/[.05] p-3 text-xs text-emerald-100">{notice}</p>}
    {quizzes === null && !loadingError && <p role="status" className="mt-4 text-xs text-slate-400">Loading today’s quizzes…</p>}
    {quizzes?.length === 0 && !loadingError && <p className="mt-4 rounded-lg border border-white/[.08] p-4 text-center text-xs text-slate-400">There are no recovery quizzes due today.</p>}
    {!!quizzes?.length && <div className="mt-4 grid gap-3">
      {quizzes.map((quiz) => <form
        key={quiz.task_id}
        className="rounded-xl border border-rose-300/15 bg-rose-300/[.025] p-4"
        onSubmit={(event) => void submitQuiz(event, quiz)}
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="eyebrow">{quiz.subject}{quiz.topic ? ` · ${quiz.topic}` : ''}</p>
            <h3 className="mt-1 text-sm font-semibold text-white">{quiz.title}</h3>
            <p className="mt-1 text-[10px] text-slate-400">Day {quiz.day_no}{quiz.scheduled_date ? ` · ${quiz.scheduled_date}` : ''} · {quiz.max_score} marks</p>
          </div>
          <span className="rounded-full border border-white/10 px-2.5 py-1 text-[10px] text-slate-300">{quiz.questions.length} questions</span>
        </div>
        {quiz.questions.map((question, questionIndex) => <fieldset className="mt-4 rounded-lg border border-white/[.08] p-3" key={`${quiz.task_id}:${questionIndex}`}>
          <legend className="px-1 text-xs font-medium text-slate-100">{questionIndex + 1}. {question.question} · {question.marks} marks</legend>
          {question.question_type === 'MCQ'
            ? <div className="mt-1 grid gap-2">
              {question.options.map((option, optionIndex) => {
                const optionKey = String.fromCharCode(65 + optionIndex);
                return <label className="flex cursor-pointer items-start gap-2 text-xs leading-5 text-slate-300" key={`${quiz.task_id}:${questionIndex}:${optionKey}`}>
                  <input
                    type="radio"
                    name={`recovery-${quiz.task_id}-${questionIndex}`}
                    value={optionKey}
                    checked={answers[quiz.task_id]?.[String(questionIndex)] === optionKey}
                    onChange={(event) => updateAnswer(quiz.task_id, questionIndex, event.target.value)}
                    disabled={busy === quiz.task_id}
                    required
                  />
                  <span><strong>{optionKey}.</strong> {option}</span>
                </label>;
              })}
            </div>
            : <label className="mt-2 block text-xs text-slate-300">
              Your answer
              <input
                type="text"
                maxLength={2000}
                required
                className="form-input"
                value={answers[quiz.task_id]?.[String(questionIndex)] ?? ''}
                onChange={(event) => updateAnswer(quiz.task_id, questionIndex, event.target.value)}
                disabled={busy === quiz.task_id}
                autoComplete="off"
              />
            </label>}
        </fieldset>)}
        <button type="submit" className="mt-3 inline-flex min-h-10 items-center rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50" disabled={busy === quiz.task_id}>
          {busy === quiz.task_id ? 'Grading…' : 'Submit and see your score'}
        </button>
      </form>)}
    </div>}
    {Object.entries(results).map(([taskId, result]) => <details className="mt-3 rounded-lg border border-white/[.08] p-3" key={taskId}>
      <summary className="cursor-pointer text-xs font-semibold text-slate-200">
        Submitted quiz · {result.score}/{result.max_score} ({result.percent}%)
      </summary>
      <ol className="mt-3 space-y-3">{result.results.map((item, index) => <li className="text-xs leading-5" key={`${taskId}:${index}`}>
        <p className="text-slate-200">{item.correct ? 'Correct' : 'Review'} · {item.question}</p>
        {!item.correct && <p className="text-slate-400">Answer: {item.correct_answer}</p>}
        <p className="text-slate-400">{item.explanation}</p>
      </li>)}</ol>
    </details>)}
  </section>;
}

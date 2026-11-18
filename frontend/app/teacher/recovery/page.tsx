'use client';

import './recovery.css';

import { AlertCircle, ArrowRight, BookOpen, Check, ChevronRight, Moon, RotateCw, Search, Sun, Upload, UsersRound, WandSparkles } from 'lucide-react';
import { ChangeEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';

type RecoveryTopic = { id: number; module_no: number; name: string; included: boolean };
type RecoveryQuestion = {
  question: string;
  question_type: 'MCQ' | 'SHORT_ANSWER';
  options: string[];
  correct_answer: string;
  explanation: string;
  marks: number;
  source_excerpt: string;
};
type RecoveryQuiz = {
  id: number;
  topic_id: number | null;
  quiz_key: string;
  max_score: number;
  questions: RecoveryQuestion[];
};
type RecoveryJob = {
  id: number;
  kind: 'topics' | 'quizzes';
  status: 'queued' | 'running' | 'completed' | 'failed';
  attempts: number;
  error_message: string | null;
  retryable: boolean;
};
type RecoveryPlan = {
  id: number;
  student_id: number;
  subject_id: number;
  source_exam: string;
  marks: number;
  selected_modules: number[];
  topics_per_day: number;
  status: 'draft' | 'topics_ready' | 'quizzes_ready' | 'assigned';
  start_date: string | null;
  topics: RecoveryTopic[];
  quizzes: RecoveryQuiz[];
  jobs: RecoveryJob[];
};
type StudentSubject = {
  id: number;
  name: string;
  code: string;
  fa1: number | null;
  maximum: number;
  academic_period: string | null;
  backlog: boolean;
  evidence_source: string | null;
  plan: RecoveryPlan | null;
};
type RecoveryStudent = {
  id: number;
  student_id: string;
  name: string;
  section: string | null;
  course_name: string | null;
  subjects: StudentSubject[];
};
type RecoveryModule = { module_no: number; status: string; filename: string; uploaded: boolean };
type RecoveryOverview = {
  stats: {
    students_with_backlogs: number;
    backlog_subjects: number;
    quizzes_assigned: number;
    average_recovery_score: number | null;
  };
  pass_mark: number;
  maximum_marks: number;
  subjects: { id: number; name: string; code: string; modules: RecoveryModule[] }[];
  students: RecoveryStudent[];
};
type PerformanceDay = { date: string; score: number; max_score: number; percent: number; attempts: number };
type ToastMessage = { message: string; tone: 'success' | 'error' };

const STEPS = ['Modules', 'Topics', 'Quizzes', 'Assigned'] as const;
const MODULE_NUMBERS = [1, 2, 3, 4, 5];
const TOPICS_PER_DAY = [1, 2, 3, 5];

function localDateValue(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The recovery request failed. Please try again.';
}

function formattedDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function currentStep(status: RecoveryPlan['status'] | null): number {
  if (status === 'topics_ready') return 1;
  if (status === 'quizzes_ready') return 2;
  if (status === 'assigned') return 3;
  return 0;
}

export default function TeacherRecoveryPage() {
  const [overview, setOverview] = useState<RecoveryOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [search, setSearch] = useState('');
  const [selectedStudentId, setSelectedStudentId] = useState<number | null>(null);
  const [selectedSubjectId, setSelectedSubjectId] = useState<number | null>(null);
  const [performance, setPerformance] = useState<PerformanceDay[] | null>(null);
  const [performanceError, setPerformanceError] = useState('');
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [jobStatusError, setJobStatusError] = useState('');
  const [reviewTopics, setReviewTopics] = useState<Record<number, boolean>>({});
  const [startDate, setStartDate] = useState(localDateValue);
  const [busyAction, setBusyAction] = useState('');
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const [theme, setTheme] = useState<'system' | 'light' | 'dark'>('system');
  const [systemDark, setSystemDark] = useState(false);

  const loadOverview = useCallback(async () => {
    const data = await api<RecoveryOverview>('/api/v1/teacher/recovery/overview');
    setOverview(data);
    setLoadError('');
    setLoading(false);
    return data;
  }, []);

  const notify = useCallback((message: string, tone: ToastMessage['tone']) => {
    setToast({ message, tone });
    window.setTimeout(() => setToast((current) => current?.message === message ? null : current), 4500);
  }, []);

  useEffect(() => {
    const queryStudent = new URLSearchParams(window.location.search).get('student_id');
    if (queryStudent && Number.isInteger(Number(queryStudent)) && Number(queryStudent) > 0) {
      setSelectedStudentId(Number(queryStudent));
    }
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    setSystemDark(media.matches);
    const updateSystemTheme = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener('change', updateSystemTheme);
    return () => media.removeEventListener('change', updateSystemTheme);
  }, []);

  useEffect(() => {
    loadOverview().catch((requestError: Error) => {
      setLoadError(requestError.message);
      setLoading(false);
    });
  }, [loadOverview]);

  const selectedStudent = useMemo(
    () => overview?.students.find((student) => student.id === selectedStudentId) ?? null,
    [overview, selectedStudentId],
  );
  const backlogSubjects = useMemo(
    () => selectedStudent?.subjects.filter((subject) => subject.backlog) ?? [],
    [selectedStudent],
  );
  const selectedSubject = useMemo(
    () => backlogSubjects.find((subject) => subject.id === selectedSubjectId) ?? null,
    [backlogSubjects, selectedSubjectId],
  );
  const selectedLibrary = useMemo(
    () => overview?.subjects.find((subject) => subject.id === selectedSubject?.id) ?? null,
    [overview, selectedSubject],
  );
  const selectedPlan = selectedSubject?.plan ?? null;
  const visibleStudents = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return (overview?.students ?? []).filter((student) => (
      !query
      || [
        student.name,
        student.student_id,
        student.section,
        student.section ? `Class ${student.section}` : null,
        student.course_name,
      ]
        .some((value) => value?.toLocaleLowerCase().includes(query))
    ));
  }, [overview, search]);

  useEffect(() => {
    if (!overview) return;
    if (selectedStudentId === null || !overview.students.some((student) => student.id === selectedStudentId)) {
      const firstWithBacklog = overview.students.find((student) => student.subjects.some((subject) => subject.backlog));
      const first = firstWithBacklog ?? overview.students[0];
      setSelectedStudentId(first?.id ?? null);
      setSelectedSubjectId(first?.subjects.find((subject) => subject.backlog)?.id ?? null);
    }
  }, [overview, selectedStudentId]);

  useEffect(() => {
    if (!selectedStudent) return;
    if (!backlogSubjects.some((subject) => subject.id === selectedSubjectId)) {
      setSelectedSubjectId(backlogSubjects[0]?.id ?? null);
    }
  }, [backlogSubjects, selectedStudent, selectedSubjectId]);

  useEffect(() => {
    if (!selectedStudent) {
      setPerformance(null);
      return;
    }
    let active = true;
    setPerformanceLoading(true);
    setPerformanceError('');
    api<{ items: PerformanceDay[] }>(`/api/v1/teacher/recovery/students/${selectedStudent.id}/performance`)
      .then((response) => { if (active) setPerformance(response.items); })
      .catch((requestError: Error) => { if (active) setPerformanceError(requestError.message); })
      .finally(() => { if (active) setPerformanceLoading(false); });
    return () => { active = false; };
  }, [selectedStudent]);

  useEffect(() => {
    setReviewTopics(Object.fromEntries((selectedPlan?.topics ?? []).map((topic) => [topic.id, topic.included])));
  }, [selectedPlan?.id, selectedPlan?.topics]);

  const updatePlanInOverview = useCallback((studentId: number, subjectId: number, plan: RecoveryPlan) => {
    setOverview((current) => current ? ({
      ...current,
      students: current.students.map((student) => student.id !== studentId ? student : ({
        ...student,
        subjects: student.subjects.map((subject) => subject.id === subjectId ? { ...subject, plan } : subject),
      })),
    }) : current);
  }, []);

  const activeJobIds = (selectedPlan?.jobs ?? [])
    .filter((job) => (job.status === 'queued' || job.status === 'running') && !job.retryable)
    .map((job) => job.id);
  const activeJobKey = activeJobIds.join(',');
  const targetPlanId = selectedPlan?.id;

  useEffect(() => {
    if (!activeJobKey || busyAction || targetPlanId === undefined || selectedStudentId === null || selectedSubjectId === null) return;
    const jobIds = activeJobKey.split(',').map(Number);
    const planId = targetPlanId;
    const studentId = selectedStudentId;
    const subjectId = selectedSubjectId;
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const jobs = await Promise.all(jobIds.map((id) => api<RecoveryJob>(`/api/v1/teacher/recovery/jobs/${id}`)));
        if (!active) return;
        setJobStatusError('');
        const jobsById = new Map(jobs.map((job) => [job.id, job]));
        setOverview((current) => current ? ({
          ...current,
          students: current.students.map((student) => student.id !== studentId ? student : ({
            ...student,
            subjects: student.subjects.map((subject) => {
              const plan = subject.plan;
              if (subject.id !== subjectId || plan?.id !== planId) return subject;
              return {
                ...subject,
                plan: {
                  ...plan,
                  jobs: plan.jobs.map((job) => jobsById.get(job.id) ?? job),
                },
              };
            }),
          })),
        }) : current);
        if (jobs.every((job) => job.status === 'completed' || job.status === 'failed')) {
          await loadOverview();
        }
      } catch (requestError) {
        if (active) setJobStatusError(errorText(requestError));
      } finally {
        polling = false;
      }
    };
    const interval = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [activeJobKey, busyAction, loadOverview, selectedStudentId, selectedSubjectId, targetPlanId]);

  const refresh = async () => {
    try {
      await loadOverview();
    } catch (requestError) {
      setLoadError(errorText(requestError));
      notify(errorText(requestError), 'error');
    }
  };

  const waitForJob = async (jobId: number): Promise<void> => {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      const job = await api<RecoveryJob>(`/api/v1/teacher/recovery/jobs/${jobId}`);
      if (job.status === 'completed') return;
      if (job.status === 'failed') {
        throw new Error(job.error_message || 'AI generation failed. Retry the job to try again.');
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 1500));
    }
    throw new Error('AI generation is still running. Reload this page to continue checking its status.');
  };

  const saveSelection = async (
    studentId: number,
    subject: StudentSubject,
    moduleNumbers: number[],
    topicsPerDay = subject.plan?.topics_per_day ?? 3,
  ): Promise<RecoveryPlan> => {
    let plan = subject.plan;
    if (!plan) {
      plan = await api<RecoveryPlan>(
        `/api/v1/teacher/recovery/students/${studentId}/subjects/${subject.id}/plans`,
        {
          method: 'POST',
          body: JSON.stringify({ selected_modules: moduleNumbers, topics_per_day: topicsPerDay }),
        },
      );
    } else {
      plan = await api<RecoveryPlan>(`/api/v1/teacher/recovery/plans/${plan.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ selected_modules: moduleNumbers, topics_per_day: topicsPerDay }),
      });
    }
    updatePlanInOverview(studentId, subject.id, plan);
    return plan;
  };

  const uploadOne = async (subjectId: number, moduleNo: number, file: File) => {
    const key = `${subjectId}:${moduleNo}`;
    setUploading((current) => ({ ...current, [key]: true }));
    try {
      const form = new FormData();
      form.append('file', file);
      await api(`/api/v1/teacher/recovery/subjects/${subjectId}/modules/${moduleNo}/upload`, {
        method: 'POST',
        body: form,
      });
      await loadOverview();
      notify(`Module ${moduleNo} uploaded and processed.`, 'success');
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setUploading((current) => ({ ...current, [key]: false }));
    }
  };

  const uploadAll = async (subjectId: number, event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = '';
    if (!files.length) return;
    if (files.length > 5) {
      notify('Choose at most five module PDFs.', 'error');
      return;
    }
    const key = `${subjectId}:all`;
    setUploading((current) => ({ ...current, [key]: true }));
    try {
      const form = new FormData();
      files.forEach((file, index) => {
        form.append('files', file);
        form.append('module_numbers', String(index + 1));
      });
      await api(`/api/v1/teacher/recovery/subjects/${subjectId}/modules/upload-all`, {
        method: 'POST',
        body: form,
      });
      await loadOverview();
      notify(`${files.length} module PDF${files.length === 1 ? '' : 's'} uploaded and processed.`, 'success');
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setUploading((current) => ({ ...current, [key]: false }));
    }
  };

  const analyzePlan = async (studentId: number, subject: StudentSubject, plan: RecoveryPlan) => {
    if (plan.status !== 'draft') return plan;
    const response = await api<{ job: RecoveryJob | null; cached: boolean; plan: RecoveryPlan }>(
      `/api/v1/teacher/recovery/plans/${plan.id}/analyze`,
      { method: 'POST' },
    );
    updatePlanInOverview(studentId, subject.id, response.plan);
    if (response.job) await waitForJob(response.job.id);
    await loadOverview();
    return response.plan;
  };

  const generatePlanQuizzes = async (studentId: number, subject: StudentSubject, plan: RecoveryPlan) => {
    if (plan.status === 'topics_ready') {
      const decisions = plan.topics.map((topic) => ({
        topic_id: topic.id,
        included: reviewTopics[topic.id] ?? topic.included,
      }));
      plan = await api<RecoveryPlan>(`/api/v1/teacher/recovery/plans/${plan.id}/topics`, {
        method: 'PATCH',
        body: JSON.stringify({ topics: decisions }),
      });
      updatePlanInOverview(studentId, subject.id, plan);
    }
    const response = await api<{ job: RecoveryJob | null; cached: boolean; plan: RecoveryPlan }>(
      `/api/v1/teacher/recovery/plans/${plan.id}/generate-quizzes`,
      { method: 'POST' },
    );
    updatePlanInOverview(studentId, subject.id, response.plan);
    if (response.job) await waitForJob(response.job.id);
    await loadOverview();
  };

  const runSingleAction = async (action: 'analyze' | 'quizzes') => {
    if (!selectedStudent || !selectedSubject) return;
    const operationId = `${action}:${selectedStudent.id}:${selectedSubject.id}`;
    setBusyAction(operationId);
    try {
      let plan = selectedSubject.plan;
      if (!plan && action === 'analyze') {
        plan = await saveSelection(selectedStudent.id, selectedSubject, []);
      }
      if (!plan) throw new Error('Choose at least one uploaded module before analysis.');
      if (action === 'analyze') {
        if (!plan.selected_modules.length) throw new Error('Choose at least one uploaded module before analysis.');
        plan = await analyzePlan(selectedStudent.id, selectedSubject, plan);
        notify('Module analysis is ready for teacher review.', 'success');
      } else {
        if (plan.status !== 'topics_ready') throw new Error('Review extracted topics before generating quizzes.');
        await generatePlanQuizzes(selectedStudent.id, selectedSubject, plan);
        notify('Quiz drafts are ready for teacher review.', 'success');
      }
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const analyzeAndPrepareAll = async () => {
    if (!selectedStudent) return;
    setBusyAction(`all:${selectedStudent.id}`);
    const prepared: string[] = [];
    const skipped: string[] = [];
    const failed: string[] = [];
    try {
      for (const subject of backlogSubjects) {
        try {
          const library = overview?.subjects.find((item) => item.id === subject.id);
          const available = library?.modules.filter((module) => module.uploaded).map((module) => module.module_no) ?? [];
          if (!available.length) {
            skipped.push(subject.name);
            continue;
          }
          let plan = subject.plan;
          if (!plan) {
            plan = await saveSelection(selectedStudent.id, subject, available);
          } else if (plan.status === 'draft' && !plan.selected_modules.length) {
            plan = await saveSelection(selectedStudent.id, subject, available);
          }
          if (!plan.selected_modules.length) {
            skipped.push(subject.name);
            continue;
          }
          plan = await analyzePlan(selectedStudent.id, subject, plan);
          const latest = (await loadOverview()).students
            .find((student) => student.id === selectedStudent.id)?.subjects
            .find((item) => item.id === subject.id)?.plan ?? plan;
          if (latest.status === 'topics_ready') {
            await generatePlanQuizzes(selectedStudent.id, subject, latest);
          }
          prepared.push(subject.name);
        } catch (requestError) {
          failed.push(`${subject.name}: ${errorText(requestError)}`);
        }
      }
      if (failed.length) notify(`Prepared ${prepared.length} subject(s). Review failures: ${failed.join(' · ')}`, 'error');
      else if (skipped.length) notify(`Prepared ${prepared.length} subject(s). Upload modules for: ${skipped.join(', ')}.`, 'success');
      else notify(`Prepared ${prepared.length} subject(s). Review every quiz before assigning.`, 'success');
    } finally {
      setBusyAction('');
    }
  };

  const assignPlan = async () => {
    if (!selectedPlan || !selectedStudent || !selectedSubject) return;
    const key = `assign:${selectedPlan.id}`;
    setBusyAction(key);
    try {
      const params = new URLSearchParams({ start_date: startDate, topics_per_day: String(selectedPlan.topics_per_day) });
      const plan = await api<RecoveryPlan>(`/api/v1/teacher/recovery/plans/${selectedPlan.id}/assign?${params}`, {
        method: 'POST',
      });
      updatePlanInOverview(selectedStudent.id, selectedSubject.id, plan);
      await loadOverview();
      notify('Recovery quizzes assigned and the student was notified.', 'success');
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const retryJob = async (jobId: number) => {
    setBusyAction(`retry:${jobId}`);
    try {
      const job = await api<RecoveryJob>(`/api/v1/teacher/recovery/jobs/${jobId}/retry`, { method: 'POST' });
      await waitForJob(job.id);
      await loadOverview();
      notify('AI generation completed. Review the saved results.', 'success');
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const updateTopicsPerDay = async (value: number) => {
    if (!selectedPlan || !selectedStudent || !selectedSubject || selectedPlan.status === 'assigned') return;
    setBusyAction(`pace:${selectedPlan.id}`);
    try {
      const plan = await api<RecoveryPlan>(`/api/v1/teacher/recovery/plans/${selectedPlan.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ topics_per_day: value }),
      });
      updatePlanInOverview(selectedStudent.id, selectedSubject.id, plan);
      await loadOverview();
    } catch (requestError) {
      notify(errorText(requestError), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const selectStudent = (student: RecoveryStudent) => {
    setSelectedStudentId(student.id);
    setSelectedSubjectId(student.subjects.find((subject) => subject.backlog)?.id ?? null);
  };

  const darkMode = theme === 'dark' || (theme === 'system' && systemDark);
  const studentBacklogCount = selectedStudent?.subjects.filter((subject) => subject.backlog).length ?? 0;

  if (loading && !overview) {
    return <main className="recovery-page" aria-busy="true"><div className="recovery-wrap">
      <p className="recovery-eyebrow eyebrow">Teacher recovery · AI-assisted</p>
      <h1 className="recovery-title">Subject recovery</h1>
      <div className="recovery-alert mt-4" role="status">Loading your classes and FA-1 results…</div>
    </div></main>;
  }

  if (loadError && !overview) {
    return <main className="recovery-page"><div className="recovery-wrap">
      <p className="eyebrow">Teacher recovery · AI-assisted</p><h1 className="recovery-title mt-2">Subject recovery</h1>
      <div role="alert" className="recovery-alert mt-4">{loadError}</div>
      <button type="button" className="recovery-button mt-3" onClick={() => {
        setLoading(true);
        loadOverview().catch((requestError: Error) => { setLoadError(requestError.message); setLoading(false); });
      }}><RotateCw size={14} /> Retry loading recovery data</button>
    </div></main>;
  }

  return <main className="recovery-page" data-theme={theme} aria-label="Subject recovery workspace">
    <div className="recovery-wrap">
      <header className="recovery-top">
        <div>
          <p className="eyebrow">Teacher recovery · AI-assisted</p>
          <h1 className="recovery-title mt-2">Subject recovery</h1>
          <p className="recovery-subtitle">
            Review real FA-1 results below {overview?.pass_mark}/{overview?.maximum_marks}, select uploaded modules,
            then review source-grounded topics and quizzes before assigning them.
          </p>
        </div>
        <button
          type="button"
          className="recovery-button"
          onClick={() => setTheme(darkMode ? 'light' : 'dark')}
          aria-label={`Switch to ${darkMode ? 'light' : 'dark'} mode`}
          aria-pressed={theme !== 'system'}
        >
          {darkMode ? <Sun size={15} /> : <Moon size={15} />}
          {darkMode ? 'Light mode' : 'Dark mode'}
        </button>
      </header>

      {loadError && <div role="alert" className="recovery-alert mb-3">{loadError}</div>}

      <section className="recovery-stats" aria-label="Recovery statistics">
        <article className="recovery-stat">
          <span className="recovery-stat-label">Students with backlogs</span>
          <strong className="recovery-stat-value">{overview?.stats.students_with_backlogs ?? '—'}</strong>
          <span className="recovery-stat-detail">Below the configured FA-1 pass mark</span>
        </article>
        <article className="recovery-stat">
          <span className="recovery-stat-label">Backlog subjects</span>
          <strong className="recovery-stat-value">{overview?.stats.backlog_subjects ?? '—'}</strong>
          <span className="recovery-stat-detail">Calculated from the latest recorded FA-1</span>
        </article>
        <article className="recovery-stat">
          <span className="recovery-stat-label">Quizzes assigned</span>
          <strong className="recovery-stat-value">{overview?.stats.quizzes_assigned ?? '—'}</strong>
          <span className="recovery-stat-detail">Topic and plan-level quizzes, not daily tasks</span>
        </article>
        <article className="recovery-stat">
          <span className="recovery-stat-label">Average recovery score</span>
          <strong className="recovery-stat-value">
            {overview?.stats.average_recovery_score === null ? '—' : `${overview?.stats.average_recovery_score ?? '—'}%`}
          </strong>
          <span className="recovery-stat-detail">From submitted quiz attempts</span>
        </article>
      </section>

      <section className="recovery-card recovery-library" aria-labelledby="recovery-library-title">
        <div className="recovery-box-heading">
          <div>
            <h2 id="recovery-library-title" className="text-xl font-bold">Module library</h2>
            <p className="recovery-muted mt-1 text-sm">Upload each subject’s five text-searchable PDF modules once. Uploaded content is reused across students.</p>
          </div>
          <BookOpen size={19} aria-hidden="true" />
        </div>
        {!overview?.subjects.length
          ? <p className="recovery-muted mt-3 text-sm">No subjects are linked to your classes.</p>
          : <div className="recovery-library-grid">
            {overview.subjects.map((subject) => {
              const uploadedCount = subject.modules.filter((libraryModule) => libraryModule.uploaded).length;
              const uploadAllKey = `${subject.id}:all`;
              return <article className="recovery-library-item" key={subject.id}>
                <div className="recovery-box-heading">
                  <div><strong>{subject.name}</strong><span className="recovery-muted ml-2 text-xs">{subject.code}</span></div>
                  <span className="recovery-muted text-xs">{uploadedCount}/5 uploaded</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {MODULE_NUMBERS.map((moduleNo) => {
                    const libraryModule = subject.modules.find((item) => item.module_no === moduleNo);
                    const inputId = `recovery-upload-${subject.id}-${moduleNo}`;
                    const uploadKey = `${subject.id}:${moduleNo}`;
                    return <div key={moduleNo} className="flex items-center gap-1">
                      <span className={`recovery-chip ${libraryModule?.uploaded ? 'recovery-chip--green' : ''}`}>
                        M{moduleNo}{libraryModule?.uploaded ? <Check size={11} aria-label="Uploaded" /> : null}
                      </span>
                      <label htmlFor={inputId} className="sr-only">Upload PDF for {subject.name}, module {moduleNo}</label>
                      <input
                        id={inputId}
                        type="file"
                        accept=".pdf,application/pdf"
                        className="recovery-hidden-file"
                        aria-label={`Choose PDF for ${subject.name}, module ${moduleNo}`}
                        disabled={!!uploading[uploadKey]}
                        onChange={(event) => {
                          const file = event.currentTarget.files?.[0];
                          event.currentTarget.value = '';
                          if (file) void uploadOne(subject.id, moduleNo, file);
                        }}
                      />
                      <label htmlFor={inputId} className="recovery-button min-h-8 px-2 py-1 text-[.68rem]">
                        {uploading[uploadKey] ? 'Uploading…' : libraryModule?.uploaded ? 'Replace' : 'Upload'}
                      </label>
                    </div>;
                  })}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor={`recovery-upload-all-${subject.id}`} className="recovery-button min-h-8 px-2 py-1 text-[.68rem]">
                    <Upload size={12} />{uploading[uploadAllKey] ? 'Uploading…' : 'Upload all'}
                  </label>
                  <input
                    id={`recovery-upload-all-${subject.id}`}
                    type="file"
                    multiple
                    accept=".pdf,application/pdf"
                    className="recovery-hidden-file"
                    aria-label={`Choose multiple PDFs for ${subject.name}; selected files map in order to modules 1 through 5`}
                    onChange={(event) => void uploadAll(subject.id, event)}
                  />
                  <span className="recovery-muted text-[.68rem]">Multiple PDFs map in selection order to Module 1, 2, …</span>
                </div>
              </article>;
            })}
          </div>}
      </section>

      <div className="recovery-grid">
        <section className="recovery-card" aria-labelledby="recovery-students-title">
          <div className="recovery-box-heading">
            <div>
              <p className="eyebrow">Student-first recovery</p>
              <h2 id="recovery-students-title" className="mt-1 text-lg font-bold">Students in your classes</h2>
            </div>
            <UsersRound size={18} aria-hidden="true" />
          </div>
          <label htmlFor="recovery-student-search" className="sr-only">Search students by name, ID, class or course</label>
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--rec-muted)]" aria-hidden="true" />
            <input
              id="recovery-student-search"
              type="search"
              className="recovery-search pl-9"
              placeholder="Search name, ID, class or course"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="recovery-student-list" role="list" aria-label="Students in your classes">
            {visibleStudents.map((student) => {
              const count = student.subjects.filter((subject) => subject.backlog).length;
              return <div role="listitem" key={student.id}>
                <button
                  type="button"
                  className="recovery-student"
                  data-active={student.id === selectedStudentId}
                  onClick={() => selectStudent(student)}
                  aria-pressed={student.id === selectedStudentId}
                >
                  <span className="flex items-center justify-between gap-2">
                    <strong className="truncate">{student.name}</strong>
                    <span className={`recovery-chip ${count ? 'recovery-chip--red' : 'recovery-chip--green'}`}>
                      {count ? `${count} backlog${count === 1 ? '' : 's'}` : 'Clear'}
                    </span>
                  </span>
                  <span className="recovery-muted mt-1 block truncate text-xs">
                    {student.student_id}{student.section ? ` · Class ${student.section}` : ''}{student.course_name ? ` · ${student.course_name}` : ''}
                  </span>
                </button>
              </div>;
            })}
            {!visibleStudents.length && <p className="recovery-muted py-8 text-center text-sm">
              {search ? 'No students match this search.' : 'No students are assigned to your classes.'}
            </p>}
          </div>
          {overview && <p className="recovery-muted mt-3 text-right text-xs">
            Showing {visibleStudents.length} of {overview.students.length} students
          </p>}
        </section>

        <section className="recovery-card min-w-0" aria-label="Selected student recovery profile">
          {!selectedStudent
            ? <div className="py-12 text-center">
              <UsersRound size={24} className="mx-auto text-[var(--rec-muted)]" aria-hidden="true" />
              <p className="mt-3 font-semibold">No students to review</p>
              <p className="recovery-muted mt-1 text-sm">Students will appear when they are enrolled in one of your classes.</p>
            </div>
            : <>
              <div className="recovery-profile-heading">
                <div>
                  <p className="eyebrow">FA-1 subject results</p>
                  <h2 className="mt-1 text-2xl font-bold">{selectedStudent.name}</h2>
                  <p className="recovery-muted mt-1 text-sm">
                    {selectedStudent.student_id}{selectedStudent.section ? ` · Class ${selectedStudent.section}` : ''}
                    {selectedStudent.course_name ? ` · ${selectedStudent.course_name}` : ''}
                    {' · '}Pass mark {overview?.pass_mark}/{overview?.maximum_marks}
                  </p>
                </div>
                <span className={`recovery-chip ${studentBacklogCount ? 'recovery-chip--red' : 'recovery-chip--green'}`}>
                  {studentBacklogCount ? `${studentBacklogCount} backlog subject${studentBacklogCount === 1 ? '' : 's'}` : 'No backlogs'}
                </span>
              </div>

              <div className="recovery-marks" aria-label="Recorded subject FA-1 marks">
                {selectedStudent.subjects.map((subject) => <div
                  key={subject.id}
                  className={`recovery-mark ${subject.backlog ? 'recovery-mark--low' : ''}`}
                >
                  <small>{subject.name}</small>
                  <strong>{subject.fa1 === null ? '—' : subject.fa1}</strong>
                  {subject.fa1 !== null && <span className="recovery-muted text-sm"> / {subject.maximum}</span>}
                  {subject.academic_period && <small>{subject.academic_period}</small>}
                </div>)}
              </div>

              {studentBacklogCount > 0 && <>
                <div className="recovery-box">
                  <div className="recovery-box-heading">
                    <div>
                      <p className="eyebrow">Backlog subjects</p>
                      <h3 className="mt-1 font-bold">Review and prepare recovery</h3>
                    </div>
                    <button
                      type="button"
                      className="recovery-button recovery-button--primary"
                      disabled={!!busyAction}
                      onClick={() => void analyzeAndPrepareAll()}
                    >
                      <WandSparkles size={14} />
                      {busyAction === `all:${selectedStudent.id}` ? 'Preparing quizzes…' : `Analyze & prepare all ${studentBacklogCount}`}
                    </button>
                  </div>
                  <p className="recovery-muted mt-2 text-xs">
                    This prepares topics and quiz drafts for review; it never assigns them automatically.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2" aria-label="Select a backlog subject">
                    {backlogSubjects.map((subject) => <button
                      type="button"
                      className={`recovery-button ${selectedSubject?.id === subject.id ? 'recovery-button--primary' : ''}`}
                      key={subject.id}
                      onClick={() => setSelectedSubjectId(subject.id)}
                      aria-pressed={selectedSubject?.id === subject.id}
                    >
                      {subject.name}<span className="opacity-80">{subject.fa1}/{subject.maximum}</span>
                      <ChevronRight size={13} aria-hidden="true" />
                    </button>)}
                  </div>
                </div>

                {selectedSubject && selectedLibrary && <section className="recovery-box" aria-label={`${selectedSubject.name} recovery plan`}>
                  <div className="recovery-box-heading">
                    <div>
                      <p className="eyebrow">{selectedSubject.code}</p>
                      <h3 className="mt-1 text-xl font-bold">{selectedSubject.name}</h3>
                    </div>
                    <span className="recovery-chip recovery-chip--red">
                      FA-1: {selectedSubject.fa1}/{selectedSubject.maximum}
                    </span>
                  </div>

                  <ol className="recovery-stepper" aria-label="Recovery plan steps">
                    {STEPS.map((step, index) => <li
                      key={step}
                      className="recovery-step"
                      data-current={currentStep(selectedPlan?.status ?? null) === index}
                      data-done={currentStep(selectedPlan?.status ?? null) > index}
                      aria-current={currentStep(selectedPlan?.status ?? null) === index ? 'step' : undefined}
                    >{index + 1}. {step}</li>)}
                  </ol>

                  <p className="recovery-muted text-sm">
                    Choose only modules with an uploaded, readable PDF. Unavailable modules stay unchecked.
                  </p>
                  <div className="recovery-module-row" role="group" aria-label="Select uploaded modules">
                    {MODULE_NUMBERS.map((moduleNo) => {
                      const libraryModule = selectedLibrary.modules.find((item) => item.module_no === moduleNo);
                      const checked = selectedPlan?.selected_modules.includes(moduleNo) ?? false;
                      const locked = selectedPlan?.status === 'assigned';
                      return <label className="recovery-module" key={moduleNo}>
                        <input
                          type="checkbox"
                          checked={libraryModule?.uploaded ? checked : false}
                          disabled={!libraryModule?.uploaded || locked || !!busyAction}
                          onChange={(event) => {
                            if (!selectedStudent || !selectedSubject) return;
                            const next = new Set(selectedPlan?.selected_modules ?? []);
                            if (event.target.checked) next.add(moduleNo);
                            else next.delete(moduleNo);
                            if (selectedPlan && selectedPlan.status !== 'draft' && !window.confirm(
                              'Changing modules will remove the current topics and quiz drafts. Continue?',
                            )) return;
                            setBusyAction(`modules:${selectedSubject.id}`);
                            void saveSelection(selectedStudent.id, selectedSubject, [...next])
                              .then(() => notify('Module selection saved.', 'success'))
                              .catch((requestError: Error) => notify(requestError.message, 'error'))
                              .finally(() => setBusyAction(''));
                          }}
                        />
                        Module {moduleNo}{libraryModule?.uploaded ? '' : ' (not uploaded)'}
                      </label>;
                    })}
                  </div>

                  {selectedPlan?.jobs.filter((job) => job.status === 'failed' || job.retryable).map((job) => <div role="alert" className="recovery-alert mt-3" key={job.id}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span>{job.error_message || (job.retryable ? 'This AI job appears to have stalled.' : 'AI generation failed.')}</span>
                      <button type="button" className="recovery-button" disabled={!!busyAction} onClick={() => void retryJob(job.id)}>
                        <RotateCw size={13} /> Retry
                      </button>
                    </div>
                  </div>)}
                  {selectedPlan?.jobs.some((job) => (job.status === 'queued' || job.status === 'running') && !job.retryable) && <p role="status" className="recovery-muted mt-3 text-sm">
                    AI job is running. This page checks its persisted status until it completes.
                  </p>}
                  {jobStatusError && <p role="alert" className="recovery-alert mt-3">{jobStatusError}</p>}

                  {selectedPlan?.status === 'draft' && <>
                    {!selectedPlan.selected_modules.length && <p className="recovery-muted mt-2 text-xs">Select at least one uploaded module before analysis.</p>}
                    <button
                      type="button"
                      className="recovery-button recovery-button--primary mt-3"
                      disabled={!selectedPlan.selected_modules.length || !!busyAction}
                      onClick={() => void runSingleAction('analyze')}
                    >
                      <WandSparkles size={14} />
                      {busyAction === `analyze:${selectedStudent.id}:${selectedSubject.id}` ? 'Analyzing modules…' : 'Analyze modules and list topics'}
                    </button>
                  </>}

                  {selectedPlan && selectedPlan.status !== 'draft' && <>
                    <p className="recovery-muted mt-3 text-sm">
                      AI analyzed {selectedPlan.selected_modules.length} module{selectedPlan.selected_modules.length === 1 ? '' : 's'} and found {selectedPlan.topics.length} source-grounded topic{selectedPlan.topics.length === 1 ? '' : 's'}.
                    </p>
                    {selectedPlan.topics.map((topic) => <div className="recovery-box mt-2" key={topic.id}>
                      <div className="recovery-box-heading">
                        <span><span className="recovery-chip recovery-chip--amber mr-2">Module {topic.module_no}</span><strong>{topic.name}</strong></span>
                        <label className="recovery-module">
                          <input
                            type="checkbox"
                            checked={reviewTopics[topic.id] ?? topic.included}
                            disabled={selectedPlan.status !== 'topics_ready' || !!busyAction}
                            onChange={(event) => setReviewTopics((current) => ({ ...current, [topic.id]: event.target.checked }))}
                          />
                          Include
                        </label>
                      </div>
                    </div>)}
                    {selectedPlan.status === 'topics_ready' && <>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <label htmlFor="recovery-topics-per-day" className="text-sm font-semibold">Topics per day</label>
                        <select
                          id="recovery-topics-per-day"
                          className="recovery-button"
                          value={selectedPlan.topics_per_day}
                          disabled={!!busyAction}
                          onChange={(event) => void updateTopicsPerDay(Number(event.target.value))}
                        >
                          {TOPICS_PER_DAY.map((number) => <option value={number} key={number}>{number}</option>)}
                        </select>
                      </div>
                      <button
                        type="button"
                        className="recovery-button recovery-button--primary mt-3"
                        disabled={!!busyAction || !selectedPlan.topics.some((topic) => reviewTopics[topic.id] ?? topic.included)}
                        onClick={() => void runSingleAction('quizzes')}
                      >
                        <WandSparkles size={14} />
                        {busyAction === `quizzes:${selectedStudent.id}:${selectedSubject.id}` ? 'Generating quizzes…' : 'Generate quizzes for review'}
                      </button>
                    </>}

                    {(selectedPlan.status === 'quizzes_ready' || selectedPlan.status === 'assigned') && <>
                      <div className="mt-4">
                        <h4 className="font-bold">Quiz drafts · teacher review</h4>
                        <p className="recovery-muted mt-1 text-xs">Check every question, answer key and explanation before assignment.</p>
                        {selectedPlan.quizzes.map((quiz) => {
                          const topic = selectedPlan.topics.find((item) => item.id === quiz.topic_id);
                          return <section className="recovery-box mt-3" key={quiz.id} aria-label={topic?.name ?? 'One-line review quiz'}>
                            <div className="recovery-box-heading">
                              <h5 className="font-semibold">{topic?.name ?? 'Five one-line questions'}</h5>
                              <span className="recovery-chip">{quiz.max_score} marks</span>
                            </div>
                            {quiz.questions.map((question, index) => <div className="recovery-question" key={`${quiz.id}:${index}`}>
                              <p><strong>{index + 1}.</strong> {question.question}<span className="recovery-muted"> · {question.marks} marks</span></p>
                              {question.options.length > 0 && <ol className="recovery-muted mt-1 list-inside list-[upper-alpha]">
                                {question.options.map((option, optionIndex) => <li key={`${quiz.id}:${index}:${optionIndex}`}>{option}</li>)}
                              </ol>}
                              <p className="mt-1"><strong>Answer:</strong> {question.correct_answer}</p>
                              <p className="recovery-muted mt-1">{question.explanation}</p>
                            </div>)}
                          </section>;
                        })}
                      </div>
                      {selectedPlan.status === 'quizzes_ready' && <div className="recovery-box mt-3">
                        <div className="flex flex-wrap items-end gap-3">
                          <div>
                            <label htmlFor="recovery-start-date" className="text-sm font-semibold">Start date</label>
                            <input
                              id="recovery-start-date"
                              type="date"
                              className="recovery-search"
                              value={startDate}
                              onChange={(event) => setStartDate(event.target.value)}
                            />
                          </div>
                          <button
                            type="button"
                            className="recovery-button recovery-button--primary"
                            disabled={!!busyAction || !startDate}
                            onClick={() => void assignPlan()}
                          >
                            {busyAction === `assign:${selectedPlan.id}` ? 'Assigning…' : 'Assign reviewed quizzes'}
                            <ArrowRight size={14} />
                          </button>
                        </div>
                      </div>}
                      {selectedPlan.status === 'assigned' && <p role="status" className="recovery-chip recovery-chip--green mt-3">
                        <Check size={12} /> Assigned · {selectedPlan.topics_per_day} topic{selectedPlan.topics_per_day === 1 ? '' : 's'} per day · student notified
                      </p>}
                      <div className="mt-3">
                        <div className="recovery-box-heading">
                          <h4 className="font-semibold">Day-by-day plan</h4>
                          <label className="recovery-muted text-xs">
                            Topics per day{' '}
                            <select
                              aria-label="Topics per day"
                              className="recovery-button min-h-8 py-1"
                              value={selectedPlan.topics_per_day}
                              disabled={selectedPlan.status === 'assigned' || !!busyAction}
                              onChange={(event) => void updateTopicsPerDay(Number(event.target.value))}
                            >
                              {TOPICS_PER_DAY.map((number) => <option value={number} key={number}>{number}</option>)}
                            </select>
                          </label>
                        </div>
                        <div className="recovery-plan-grid">
                          {Array.from({
                            length: Math.ceil(selectedPlan.topics.filter((topic) => topic.included).length / selectedPlan.topics_per_day),
                          }, (_, dayIndex) => {
                            const topics = selectedPlan.topics.filter((topic) => topic.included)
                              .slice(dayIndex * selectedPlan.topics_per_day, (dayIndex + 1) * selectedPlan.topics_per_day);
                            return <div className="recovery-plan-day" key={dayIndex + 1}>
                              <strong>Day {dayIndex + 1}</strong>
                              <small>{selectedPlan.status === 'assigned' && selectedPlan.start_date
                                ? formattedDate(new Date(new Date(`${selectedPlan.start_date}T00:00:00`).getTime() + dayIndex * 86_400_000).toISOString().slice(0, 10))
                                : 'Starts when assigned'}</small>
                              {topics.map((topic) => <span key={topic.id}>{topic.name} <i className="recovery-muted">M{topic.module_no}</i></span>)}
                              {dayIndex === Math.ceil(selectedPlan.topics.filter((topic) => topic.included).length / selectedPlan.topics_per_day) - 1
                                && <span className="recovery-muted">+ one-line review</span>}
                            </div>;
                          })}
                          {!selectedPlan.topics.some((topic) => topic.included) && <p className="recovery-muted text-sm">No included topics.</p>}
                        </div>
                      </div>
                    </>}
                  </>}
                </section>}
              </>}

              {studentBacklogCount === 0 && <div className="recovery-box mt-4">
                <p className="font-semibold">No FA-1 backlogs</p>
                <p className="recovery-muted mt-1 text-sm">No recorded subject result is below the configured pass mark.</p>
              </div>}

              <section className="recovery-box mt-4" aria-labelledby="recovery-performance-title">
                <div className="recovery-box-heading">
                  <div>
                    <p className="eyebrow">Submitted student attempts</p>
                    <h3 id="recovery-performance-title" className="mt-1 text-lg font-bold">Daily performance</h3>
                  </div>
                  {performance && <span className="recovery-muted text-xs">{performance.reduce((total, item) => total + item.attempts, 0)} real attempts</span>}
                </div>
                {performanceLoading && <p role="status" className="recovery-muted mt-3 text-sm">Loading quiz results…</p>}
                {performanceError && <div role="alert" className="recovery-alert mt-3">{performanceError}</div>}
                {!performanceLoading && !performanceError && !performance?.length && <p className="recovery-muted py-8 text-center text-sm">No quiz results yet.</p>}
                {!!performance?.length && <figure className="recovery-chart" aria-label="Daily recovery quiz percentage based on recorded attempts">
                  {performance.map((day) => <div className="recovery-chart-bar" key={day.date} aria-label={`${day.date}: ${day.percent} percent from ${day.attempts} quizzes`}>
                    <strong>{day.percent}%</strong>
                    <i style={{ height: `${Math.max(3, Math.min(100, day.percent))}%` }} aria-hidden="true" />
                    <span>{formattedDate(day.date)}</span>
                  </div>)}
                </figure>}
              </section>
            </>}
        </section>
      </div>
    </div>
    {toast && <div role={toast.tone === 'error' ? 'alert' : 'status'} className="recovery-toast">
      <div className="flex items-start gap-2">
        {toast.tone === 'error' ? <AlertCircle size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}
        <span>{toast.message}</span>
        <button type="button" className="ml-auto text-xs underline" onClick={() => setToast(null)}>Dismiss</button>
      </div>
    </div>}
  </main>;
}

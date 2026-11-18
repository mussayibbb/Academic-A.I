'use client';

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Check, ChevronDown, Mail, Search, Send, Sparkles, WandSparkles, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading, RiskBadge } from '@/components/ui';
import { api } from '@/lib/api';

type Student = {
  id: number;
  full_name: string;
  email?: string | null;
  risk_level: string;
  risk_probability?: number | null;
  attendance?: number | null;
  assessment?: number | null;
  assignments_completed?: number | null;
  momentum?: Record<string, unknown> | null;
  momentum_trend?: string | null;
};

type Draft = {
  id: number;
  student_id: number;
  title: string;
  body: string;
  status: string;
  provider?: string | null;
  evidence?: Record<string, unknown>;
};

type StudentDirectory = { items: Student[]; total: number };
type DraftFields = { title: string; body: string };
type AcademicSubject = { name?: string; fa1_normalized?: number | null };
type AcademicRecords = { subjects?: Record<string, AcademicSubject> };
type AcademicState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'loaded'; data: AcademicRecords };
type Notice = { kind: 'success' | 'error'; message: string; retry?: () => void };
type EvidenceRecord = Record<string, unknown>;

const reviewHeader = 'AI-GENERATED DRAFT - FACULTY REVIEW REQUIRED';

function friendlyError(error: unknown, action: string): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/timed out|timeout/i.test(message)) {
    return `${action} timed out while Ollama generated the draft. Retry after the model finishes loading; if it continues, check LLM_TIMEOUT_SECONDS and the backend logs.`;
  }
  if (/not reachable|connection refused|no connection could be made/i.test(message)) {
    return `${action} could not reach Ollama. Start Ollama and verify the configured model is installed, then retry.`;
  }
  if (/model.*not available|not installed|missing/i.test(message)) {
    return `${action} could not run because the configured Ollama model is missing. Install the configured model, then retry.`;
  }
  if (/request failed \(500\)/i.test(message)) {
    return `${action} failed with a server error (HTTP 500). Retry once; if it persists, check the backend logs for the traceback.`;
  }
  return `${action} failed: ${message || 'The server returned no details.'} Retry or check the backend response.`;
}

function record(value: unknown): EvidenceRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as EvidenceRecord
    : {};
}

function numericValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function draftFields(draft: Draft): DraftFields {
  let body = draft.body
    .replace(new RegExp(`^\\s*${reviewHeader}\\s*`, 'gim'), '')
    .replace(/[*_`]/g, '')
    .trim();
  const subjectLine = body.match(/^subject\s*:\s*(.+)$/im);
  const title = subjectLine?.[1]?.trim() || draft.title;
  if (subjectLine) body = body.replace(subjectLine[0], '');
  body = body
    .replace(/^\s*(?:observations|recommendations)\s*:\s*/gim, '')
    .replace(/^\s*[-*]\s+/gm, '')
    .trim();
  return { title, body };
}

function wordCount(value: string): number {
  return value.trim() ? value.trim().split(/\s+/).length : 0;
}

function weakestSubjects(data: AcademicRecords | undefined): Array<{ name: string; mark: number }> {
  return Object.values(data?.subjects ?? {})
    .filter((subject): subject is AcademicSubject & { fa1_normalized: number } =>
      typeof subject.fa1_normalized === 'number' && Number.isFinite(subject.fa1_normalized))
    .map((subject) => ({ name: subject.name ?? 'Subject', mark: subject.fa1_normalized }))
    .sort((left, right) => left.mark - right.mark)
    .slice(0, 2);
}

function RequestFailure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[.05] p-3">
      <p className="text-xs leading-5 text-rose-100">{message}</p>
      <button type="button" onClick={onRetry} className="mt-2 min-h-9 rounded-md border border-rose-200/20 px-3 text-xs font-medium text-rose-100 hover:bg-rose-200/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
        Retry
      </button>
    </div>
  );
}

function SummaryMetric({
  loading,
  label,
  value,
  detail,
  color,
}: {
  loading: boolean;
  label: string;
  value: number;
  detail: string;
  color: 'rose' | 'amber' | 'mint';
}) {
  if (!loading) return <Metric label={label} value={value} detail={detail} color={color} spotlight />;
  return (
    <div aria-label={`Loading ${label}`} className="surface min-h-32 rounded-xl p-4 sm:p-5">
      <span className="text-xs font-medium text-slate-400">{label}</span>
      <div className="skeleton mt-4 h-8 w-20 rounded-md" />
      <div className="mt-2 text-[11px] text-slate-500">{detail}</div>
    </div>
  );
}

export default function InterventionsPage() {
  const reduceMotion = useReducedMotion() ?? false;
  const [students, setStudents] = useState<Student[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [studentsLoading, setStudentsLoading] = useState(true);
  const [draftsLoading, setDraftsLoading] = useState(true);
  const [studentsError, setStudentsError] = useState('');
  const [draftsError, setDraftsError] = useState('');
  const [academic, setAcademic] = useState<Record<number, AcademicState>>({});
  const [editing, setEditing] = useState<Record<number, DraftFields>>({});
  const [dismissedDraftIds, setDismissedDraftIds] = useState<number[]>([]);
  const [expandedEvidenceIds, setExpandedEvidenceIds] = useState<number[]>([]);
  const [selectedStudentId, setSelectedStudentId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [riskFilter, setRiskFilter] = useState<'all' | 'HIGH' | 'MEDIUM'>('all');
  const [sort, setSort] = useState<'risk' | 'name'>('risk');
  const [tab, setTab] = useState<'review' | 'sent'>('review');
  const [busyStudentId, setBusyStudentId] = useState<number | null>(null);
  const [busyDraftId, setBusyDraftId] = useState<number | null>(null);
  const [bulkGenerating, setBulkGenerating] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const loadStudents = useCallback(async () => {
    setStudentsLoading(true);
    setStudentsError('');
    try {
      const directory = await api<StudentDirectory>('/api/v1/students?limit=100');
      setStudents(directory.items);
    } catch (error) {
      setStudentsError(friendlyError(error, 'Loading students'));
    } finally {
      setStudentsLoading(false);
    }
  }, []);

  const loadDrafts = useCallback(async () => {
    setDraftsLoading(true);
    setDraftsError('');
    try {
      setDrafts(await api<Draft[]>('/api/v1/interventions'));
    } catch (error) {
      setDraftsError(friendlyError(error, 'Loading intervention drafts'));
    } finally {
      setDraftsLoading(false);
    }
  }, []);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadStudents(), loadDrafts()]);
  }, [loadDrafts, loadStudents]);

  useEffect(() => {
    void reloadAll();
  }, [reloadAll]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 6500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const loadAcademicRecords = async (studentId: number) => {
    const current = academic[studentId];
    if (current?.status === 'loading' || current?.status === 'loaded') return;
    setAcademic((items) => ({ ...items, [studentId]: { status: 'loading' } }));
    try {
      const data = await api<AcademicRecords>(`/api/v1/students/${studentId}/academic-records`);
      setAcademic((items) => ({ ...items, [studentId]: { status: 'loaded', data } }));
    } catch (error) {
      setAcademic((items) => ({
        ...items,
        [studentId]: { status: 'error', message: friendlyError(error, 'Loading subject marks') },
      }));
    }
  };

  const studentsToConsider = useMemo(
    () => students.filter((student) => student.risk_level !== 'LOW'),
    [students],
  );
  const visibleStudents = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const filtered = studentsToConsider.filter((student) =>
      (!query || student.full_name.toLocaleLowerCase().includes(query))
      && (riskFilter === 'all' || student.risk_level === riskFilter));
    return filtered.sort((left, right) => {
      if (sort === 'name') return left.full_name.localeCompare(right.full_name);
      const rank = (level: string) => level === 'HIGH' ? 0 : level === 'MEDIUM' ? 1 : 2;
      const riskOrder = rank(left.risk_level) - rank(right.risk_level);
      if (riskOrder) return riskOrder;
      return (right.risk_probability ?? 0) - (left.risk_probability ?? 0);
    });
  }, [riskFilter, search, sort, studentsToConsider]);

  const reviewDrafts = useMemo(
    () => drafts.filter((draft) => draft.status === 'draft' && !dismissedDraftIds.includes(draft.id)),
    [dismissedDraftIds, drafts],
  );
  const sentDrafts = useMemo(() => drafts.filter((draft) => draft.status !== 'draft'), [drafts]);
  const openDraftStudentIds = useMemo(
    () => new Set(drafts.filter((draft) => draft.status === 'draft').map((draft) => draft.student_id)),
    [drafts],
  );
  const eligibleHighRiskStudents = useMemo(
    () => studentsToConsider.filter((student) => student.risk_level === 'HIGH' && !openDraftStudentIds.has(student.id)),
    [openDraftStudentIds, studentsToConsider],
  );
  const visibleDrafts = tab === 'review' ? reviewDrafts : sentDrafts;
  const studentsById = useMemo(() => new Map(students.map((student) => [student.id, student])), [students]);

  const generateDraft = async (student: Student) => {
    if (openDraftStudentIds.has(student.id) || busyStudentId !== null || bulkGenerating) return;
    setBusyStudentId(student.id);
    setNotice(null);
    try {
      await api('/api/v1/interventions/generate', {
        method: 'POST',
        body: JSON.stringify({ student_id: student.id }),
      });
      await reloadAll();
      setNotice({ kind: 'success', message: `Draft created for ${student.full_name}. Faculty review is required before approval.` });
    } catch (error) {
      setNotice({
        kind: 'error',
        message: friendlyError(error, `Generating a draft for ${student.full_name}`),
        retry: () => void generateDraft(student),
      });
    } finally {
      setBusyStudentId(null);
    }
  };

  const generateForAllHighRisk = async () => {
    if (bulkGenerating || busyStudentId !== null) return;
    const targets = eligibleHighRiskStudents;
    if (!targets.length) {
      setNotice({ kind: 'success', message: 'No high-risk students need a new draft.' });
      return;
    }
    setBulkGenerating(true);
    setNotice(null);
    let generated = 0;
    let failed = 0;
    let lastFailure = '';
    for (const student of targets) {
      setBusyStudentId(student.id);
      try {
        await api('/api/v1/interventions/generate', {
          method: 'POST',
          body: JSON.stringify({ student_id: student.id }),
        });
        generated += 1;
      } catch (error) {
        failed += 1;
        lastFailure = friendlyError(error, `Generating a draft for ${student.full_name}`);
      }
    }
    setBusyStudentId(null);
    await reloadAll();
    setBulkGenerating(false);
    if (failed) {
      setNotice({
        kind: 'error',
        message: `${generated} drafts created and ${failed} failed. ${lastFailure}`,
        retry: () => void generateForAllHighRisk(),
      });
    } else {
      setNotice({ kind: 'success', message: `${generated} drafts created. Faculty review is required for every draft.` });
    }
  };

  const saveDraft = async (draft: Draft) => {
    const value = editing[draft.id] ?? draftFields(draft);
    if (value.title.trim().length < 2 || value.body.trim().length < 10) {
      setNotice({ kind: 'error', message: 'Saving edits failed: enter a subject and a message of at least 10 characters.' });
      return;
    }
    setBusyDraftId(draft.id);
    setNotice(null);
    try {
      await api(`/api/v1/interventions/${draft.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: value.title.trim(), body: value.body.trim() }),
      });
      setEditing((items) => {
        const updated = { ...items };
        delete updated[draft.id];
        return updated;
      });
      await loadDrafts();
      setNotice({ kind: 'success', message: 'Faculty edits saved.' });
    } catch (error) {
      setNotice({
        kind: 'error',
        message: friendlyError(error, 'Saving faculty edits'),
        retry: () => void saveDraft(draft),
      });
    } finally {
      setBusyDraftId(null);
    }
  };

  const approveDraft = async (draft: Draft) => {
    setBusyDraftId(draft.id);
    setNotice(null);
    try {
      await api(`/api/v1/interventions/${draft.id}/approve`, { method: 'POST' });
      setEditing((items) => {
        const updated = { ...items };
        delete updated[draft.id];
        return updated;
      });
      await loadDrafts();
      setNotice({ kind: 'success', message: 'Draft approved and recorded for in-app notification.' });
    } catch (error) {
      setNotice({
        kind: 'error',
        message: friendlyError(error, 'Approving the draft and notifying the student'),
        retry: () => void approveDraft(draft),
      });
    } finally {
      setBusyDraftId(null);
    }
  };

  const discardDraft = (draft: Draft) => {
    setDismissedDraftIds((ids) => ids.includes(draft.id) ? ids : [...ids, draft.id]);
    setNotice({
      kind: 'success',
      message: 'Draft hidden for this session. It remains saved and will reappear after refresh.',
    });
  };

  const toggleEvidence = (draftId: number, studentId: number) => {
    const opening = !expandedEvidenceIds.includes(draftId);
    setExpandedEvidenceIds((ids) => opening
      ? [...ids, draftId]
      : ids.filter((id) => id !== draftId));
    if (opening) void loadAcademicRecords(studentId);
  };

  return (
    <div className="fade-up">
      <PageHeading
        eyebrow="Faculty review · AI-assisted"
        title="Interventions"
        description="Review evidence, make faculty edits, and approve each support message before it is sent as an in-app notification."
        action={
          <button
            type="button"
            disabled={bulkGenerating || busyStudentId !== null || eligibleHighRiskStudents.length === 0}
            onClick={() => void generateForAllHighRisk()}
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition-colors hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
          >
            <WandSparkles size={15} />
            {bulkGenerating ? 'Drafting high-risk students...' : 'Draft for all high-risk students'}
          </button>
        }
      />

      {notice && (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          className={`mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 text-xs ${
            notice.kind === 'error'
              ? 'border-rose-300/25 bg-rose-300/[.06] text-rose-100'
              : 'border-emerald-200/20 bg-emerald-200/5 text-emerald-100'
          }`}
        >
          <span>{notice.message}</span>
          <div className="flex items-center gap-2">
            {notice.retry && (
              <button type="button" onClick={notice.retry} className="min-h-8 rounded-md border border-current/20 px-2.5 font-medium hover:bg-white/5">
                Retry
              </button>
            )}
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message" className="grid size-8 place-items-center rounded-md hover:bg-white/5">
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      <section aria-label="Intervention summary" className="mb-5 grid gap-3 sm:grid-cols-3">
        <SummaryMetric
          loading={studentsLoading}
          label="Students to consider"
          value={studentsToConsider.length}
          detail="High- and medium-risk records"
          color="rose"
        />
        <SummaryMetric
          loading={draftsLoading}
          label="Drafts awaiting review"
          value={drafts.filter((draft) => draft.status === 'draft').length}
          detail="Faculty approval required"
          color="amber"
        />
        <SummaryMetric
          loading={draftsLoading}
          label="Messages delivered"
          value={drafts.filter((draft) => draft.status !== 'draft').length}
          detail="Approved in-app intervention records"
          color="mint"
        />
      </section>

      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(19rem,.82fr)_minmax(0,1.18fr)]">
        <section aria-labelledby="students-to-consider-title" className="surface min-w-0 rounded-xl p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <WandSparkles size={16} className="text-rose-200" />
              <h2 id="students-to-consider-title" className="text-sm font-semibold">Students to consider</h2>
              <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] tabular-nums text-slate-400">
                {studentsLoading ? '—' : studentsToConsider.length}
              </span>
            </div>
            <span className="text-[10px] text-slate-500">A star creates a draft; selecting a row does not.</span>
          </div>

          <label className="relative mt-4 block">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <span className="sr-only">Search students by name</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by student name"
              className="form-input !mt-0 h-10 pl-9 text-xs"
            />
          </label>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <div role="group" aria-label="Filter students by risk" className="inline-flex rounded-lg border border-white/10 bg-black/10 p-1">
              {([
                ['all', 'All'],
                ['HIGH', 'High risk'],
                ['MEDIUM', 'Medium risk'],
              ] as const).map(([value, label]) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={riskFilter === value}
                  onClick={() => setRiskFilter(value)}
                  className={`min-h-8 rounded-md px-2.5 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 ${
                    riskFilter === value ? 'bg-rose-500/15 text-rose-100' : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-[10px] text-slate-500">
              Sort
              <select
                aria-label="Sort students"
                value={sort}
                onChange={(event) => setSort(event.target.value as 'risk' | 'name')}
                className="h-9 rounded-lg border border-white/10 bg-[#151619] px-2 text-[10px] text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
              >
                <option value="risk">Highest risk first</option>
                <option value="name">Name</option>
              </select>
            </label>
          </div>

          {studentsError && <RequestFailure message={studentsError} onRetry={() => void loadStudents()} />}
          {studentsLoading && students.length === 0 && (
            <div aria-label="Loading students" className="mt-4 space-y-2">
              {[0, 1, 2, 3].map((item) => <div key={item} className="skeleton h-28 rounded-lg" />)}
            </div>
          )}
          {!studentsLoading && !studentsError && visibleStudents.length === 0 && (
            <div className="mt-4 rounded-lg border border-dashed border-white/10 p-5">
              <Empty title={students.length ? 'No students match these filters' : 'No students to consider'} detail="Try another search or risk filter, or retry loading the student list." />
              {students.length === 0 && (
                <button type="button" onClick={() => void loadStudents()} className="mt-3 min-h-9 w-full rounded-md border border-white/10 text-xs text-slate-300 hover:bg-white/5">Retry</button>
              )}
            </div>
          )}

          <div className="mt-3 max-h-[68vh] space-y-2 overflow-y-auto pr-1">
            {visibleStudents.map((student) => {
              const isSelected = selectedStudentId === student.id;
              const openDraft = openDraftStudentIds.has(student.id);
              const isGenerating = busyStudentId === student.id;
              const attendance = numericValue(student.attendance);
              const assignmentsCompleted = numericValue(student.assignments_completed);
              const classProbability = numericValue(student.risk_probability);
              const probabilityPercent = classProbability === null
                ? null
                : Math.round(Math.max(0, Math.min(1, classProbability)) * 100);
              const academicState = academic[student.id];
              const weakest = academicState?.status === 'loaded' ? weakestSubjects(academicState.data) : [];
              return (
                <article key={student.id} className={`rounded-lg border transition-colors ${
                  isSelected ? 'border-rose-300/35 bg-rose-300/[.055]' : 'border-white/[.07] bg-black/[.12] hover:border-white/[.14]'
                }`}>
                  <div className="flex items-start gap-2 p-3">
                    <button
                      type="button"
                      aria-label={`Select student ${student.full_name}`}
                      aria-pressed={isSelected}
                      onClick={() => {
                        setSelectedStudentId(student.id);
                        void loadAcademicRecords(student.id);
                      }}
                      className="min-w-0 flex-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="truncate text-xs font-semibold text-slate-100">{student.full_name}</span>
                        <RiskBadge level={student.risk_level} />
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
                        <span className="text-slate-500">Attendance <b className="ml-1 font-medium text-slate-200">{attendance === null ? '—' : `${attendance.toFixed(0)}%`}</b></span>
                        <span className="text-right text-slate-500">Activity <b className="ml-1 font-medium text-slate-200">{assignmentsCompleted === null ? '—' : `${assignmentsCompleted} assignments`}</b></span>
                      </div>
                      {probabilityPercent !== null && (
                        <div className="mt-2" aria-label={`${student.risk_level} model class probability ${probabilityPercent}%`}>
                          <div className="mb-1 flex justify-between text-[9px] text-slate-500">
                            <span>Model class probability</span>
                            <span>{probabilityPercent}%</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-white/[.07]">
                            <motion.div
                              initial={reduceMotion ? false : { width: 0 }}
                              animate={{ width: `${probabilityPercent}%` }}
                              transition={{ duration: reduceMotion ? 0 : 0.65, ease: 'easeOut' }}
                              className={`h-full rounded-full ${student.risk_level === 'HIGH' ? 'bg-rose-400' : 'bg-amber-200'}`}
                            />
                          </div>
                        </div>
                      )}
                      <div className="mt-2 min-h-4 text-[10px] text-slate-500">
                        <span className="text-slate-400">Weakest subjects: </span>
                        {academicState?.status === 'loading' && 'Loading verified marks...'}
                        {academicState?.status === 'error' && (
                          <span className="text-rose-200">
                            {academicState.message}
                          </span>
                        )}
                        {academicState?.status === 'loaded' && (weakest.length
                          ? weakest.map((item) => `${item.name} (${item.mark.toFixed(1)}/25)`).join(' · ')
                          : 'No verified FA-1 marks recorded.')}
                        {!academicState && 'Select the student to load verified marks'}
                      </div>
                    </button>
                    <button
                      type="button"
                      disabled={openDraft || isGenerating || busyStudentId !== null || bulkGenerating}
                      onClick={() => void generateDraft(student)}
                      title={openDraft ? 'An open draft already exists' : `Generate draft for ${student.full_name}`}
                      aria-label={openDraft ? `Draft already open for ${student.full_name}` : `Generate draft for ${student.full_name}`}
                      className="group mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg border border-white/10 text-rose-100 transition-colors hover:border-rose-300/30 hover:bg-rose-300/10 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                    >
                      <Sparkles size={15} className={`transition-transform duration-200 group-hover:rotate-45 ${isGenerating ? 'animate-spin' : ''}`} />
                    </button>
                  </div>
                  {academicState?.status === 'error' && (
                    <div className="flex items-center justify-between gap-2 border-t border-white/[.06] px-3 py-2">
                      <span className="text-[9px] text-slate-500">Subject marks could not be loaded.</span>
                      <button type="button" onClick={() => void loadAcademicRecords(student.id)} className="min-h-8 rounded-md border border-white/10 px-2.5 text-[10px] text-slate-300 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
                        Retry
                      </button>
                    </div>
                  )}
                  {isGenerating && (
                    <div className="skeleton mx-3 mb-3 h-1 rounded-full" aria-label={`Generating draft for ${student.full_name}`} />
                  )}
                  {openDraft && <p className="px-3 pb-3 text-[9px] text-slate-500">An open draft already exists for this student.</p>}
                </article>
              );
            })}
          </div>
        </section>

        <section aria-labelledby="drafts-title" className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="eyebrow">Faculty workspace</p>
              <h2 id="drafts-title" className="mt-1 text-lg font-semibold text-white">Intervention drafts</h2>
            </div>
            <div role="tablist" aria-label="Intervention messages" className="inline-flex rounded-lg border border-white/10 bg-black/10 p-1">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'review'}
                onClick={() => setTab('review')}
                className={`min-h-9 rounded-md px-3 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 ${
                  tab === 'review' ? 'bg-rose-500/15 text-rose-100' : 'text-slate-400 hover:text-white'
                }`}
              >
                Needs review <span className="ml-1 tabular-nums">{draftsLoading ? '—' : reviewDrafts.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'sent'}
                onClick={() => setTab('sent')}
                className={`min-h-9 rounded-md px-3 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 ${
                  tab === 'sent' ? 'bg-rose-500/15 text-rose-100' : 'text-slate-400 hover:text-white'
                }`}
              >
                Sent · in-app <span className="ml-1 tabular-nums">{draftsLoading ? '—' : sentDrafts.length}</span>
              </button>
            </div>
          </div>

          {draftsError && <RequestFailure message={draftsError} onRetry={() => void loadDrafts()} />}
          {draftsLoading && drafts.length === 0 && (
            <div aria-label="Loading intervention drafts" className="space-y-3">
              <div className="skeleton h-64 rounded-xl" />
              <div className="skeleton h-48 rounded-xl" />
            </div>
          )}
          {bulkGenerating && (
            <div role="status" className="surface mb-3 flex items-center gap-3 rounded-xl p-4">
              <div className="skeleton h-2 flex-1 rounded-full" />
              <span className="text-[10px] text-slate-400">Generating one draft at a time...</span>
            </div>
          )}
          {!draftsLoading && !draftsError && visibleDrafts.length === 0 && (
            <div className="surface rounded-xl p-5">
              <Empty
                title={tab === 'review' ? 'No drafts need review' : 'No messages have been approved'}
                detail={tab === 'review' ? "Use a student's sparkle button to create an AI draft for faculty review." : 'Approved intervention records appear here after in-app notification.'}
              />
            </div>
          )}

          <div className="space-y-3">
            <AnimatePresence initial={false}>
              {visibleDrafts.map((draft) => {
                const student = studentsById.get(draft.student_id);
                const form = editing[draft.id] ?? draftFields(draft);
                const isBusy = busyDraftId === draft.id;
                const evidence = record(draft.evidence);
                const prediction = record(evidence.risk_prediction);
                const predictionInputs = record(prediction.input_features);
                const storedAttendance = numericValue(student?.attendance) ?? numericValue(predictionInputs.attendance);
                const activity = numericValue(student?.assignments_completed) ?? numericValue(predictionInputs.assignments_completed);
                const riskScore = numericValue(prediction.risk_probability) ?? numericValue(student?.risk_probability);
                const academicState = academic[draft.student_id];
                const weakest = academicState?.status === 'loaded' ? weakestSubjects(academicState.data) : [];
                const evidenceOpen = expandedEvidenceIds.includes(draft.id);
                return (
                  <motion.article
                    key={draft.id}
                    initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -8 }}
                    transition={{ duration: reduceMotion ? 0 : 0.2, ease: 'easeOut' }}
                    className="surface overflow-hidden rounded-xl p-4 sm:p-5"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="eyebrow">AI-generated draft, faculty review required</div>
                        <h3 className="mt-1 text-sm font-semibold text-white">
                          {student?.full_name ?? 'Student unavailable'}
                        </h3>
                        <p className="mt-1 text-[10px] text-slate-500">
                          {draft.provider ?? 'Provider not reported'} · {draft.status === 'draft' ? 'Needs review' : 'Approved'}
                        </p>
                      </div>
                      <RiskBadge level={student?.risk_level ?? String(prediction.risk_level ?? 'UNKNOWN')} />
                    </div>

                    {student?.email?.trim() && (
                      <div className="mt-3 flex items-center gap-2 rounded-lg border border-white/[.07] bg-black/[.12] px-3 py-2">
                        <Mail size={13} className="shrink-0 text-slate-500" />
                        <span className="text-[10px] text-slate-500">To</span>
                        <span className="min-w-0 truncate text-xs text-slate-200">{student.email}</span>
                      </div>
                    )}

                    {draft.status === 'draft' ? (
                      <div className="mt-3 grid gap-3">
                        <label className="block text-[10px] font-medium text-slate-400">
                          Subject
                          <input
                            value={form.title}
                            onChange={(event) => setEditing((items) => ({
                              ...items,
                              [draft.id]: { title: event.target.value, body: items[draft.id]?.body ?? draftFields(draft).body },
                            }))}
                            className="form-input text-xs"
                          />
                        </label>
                        <label className="block text-[10px] font-medium text-slate-400">
                          Message body
                          <textarea
                            rows={6}
                            value={form.body}
                            onChange={(event) => setEditing((items) => ({
                              ...items,
                              [draft.id]: { title: items[draft.id]?.title ?? draftFields(draft).title, body: event.target.value },
                            }))}
                            className="form-input resize-y text-xs leading-5"
                          />
                          <span className="mt-1 block text-right text-[9px] text-slate-500">{wordCount(form.body)} words</span>
                        </label>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => void saveDraft(draft)}
                            className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] font-medium text-slate-200 transition-colors hover:bg-white/5 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                          >
                            <Check size={13} /> {isBusy ? 'Saving...' : 'Save edits'}
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => discardDraft(draft)}
                            className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] text-slate-400 transition-colors hover:text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                          >
                            <X size={13} /> Discard
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => void approveDraft(draft)}
                            className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-emerald-200 px-3 text-[10px] font-semibold text-[#07170f] transition-colors hover:bg-emerald-100 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200"
                          >
                            <Send size={13} /> {isBusy ? 'Approving...' : 'Approve and notify student'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="mt-3 whitespace-pre-wrap rounded-lg border border-white/[.07] bg-black/[.12] p-3 text-xs leading-6 text-slate-300">
                        <p className="font-medium text-slate-100">{form.title}</p>
                        <p className="mt-2">{form.body}</p>
                      </div>
                    )}

                    <div className="mt-3 border-t border-white/[.07] pt-3">
                      <button
                        type="button"
                        aria-expanded={evidenceOpen}
                        onClick={() => toggleEvidence(draft.id, draft.student_id)}
                        className="inline-flex min-h-9 items-center gap-2 text-[10px] font-medium text-slate-300 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"
                      >
                        See the evidence behind this draft
                        <ChevronDown size={13} className={`transition-transform duration-200 ${evidenceOpen ? 'rotate-180' : ''}`} />
                      </button>
                      {evidenceOpen && (
                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                          <div className="rounded-lg border border-white/[.07] bg-black/[.12] p-3">
                            <p className="text-[9px] uppercase tracking-[.12em] text-slate-500">Source record</p>
                            <dl className="mt-2 space-y-2 text-[10px]">
                              <div className="flex justify-between gap-2"><dt className="text-slate-500">Attendance</dt><dd className="text-right text-slate-200">{storedAttendance === null ? 'Not recorded' : `${storedAttendance.toFixed(1)}%`}</dd></div>
                              <div className="flex justify-between gap-2"><dt className="text-slate-500">Activity</dt><dd className="text-right text-slate-200">{activity === null ? 'Not recorded' : `${activity} assignments completed`}</dd></div>
                              <div className="flex justify-between gap-2"><dt className="text-slate-500">Model class probability</dt><dd className="text-right text-slate-200">{riskScore === null ? 'Not recorded' : `${(riskScore * 100).toFixed(1)}%`}</dd></div>
                            </dl>
                          </div>
                          <div className="rounded-lg border border-white/[.07] bg-black/[.12] p-3">
                            <p className="text-[9px] uppercase tracking-[.12em] text-slate-500">Lowest verified FA-1 marks</p>
                            {academicState?.status === 'loading' && <p className="mt-2 text-[10px] text-slate-400">Loading academic records...</p>}
                            {academicState?.status === 'error' && (
                              <div className="mt-2">
                                <p className="text-[10px] text-rose-200">{academicState.message}</p>
                                <button type="button" onClick={() => void loadAcademicRecords(draft.student_id)} className="mt-2 min-h-8 rounded-md border border-white/10 px-2 text-[10px] text-slate-200">Retry</button>
                              </div>
                            )}
                            {academicState?.status === 'loaded' && (
                              <p className="mt-2 text-[10px] leading-5 text-slate-200">
                                {weakest.length ? weakest.map((item) => `${item.name}: ${item.mark.toFixed(1)}/25`).join(' · ') : 'No verified FA-1 marks recorded.'}
                              </p>
                            )}
                            {!academicState && <p className="mt-2 text-[10px] text-slate-500">Subject marks are not part of this draft response.</p>}
                            {record(evidence.momentum) && Object.keys(record(evidence.momentum)).length > 0 && (
                              <p className="mt-2 text-[9px] text-slate-500">Momentum values are included in the source record below.</p>
                            )}
                          </div>
                          <details className="rounded-lg border border-white/[.07] bg-black/[.12] p-3 sm:col-span-2">
                            <summary className="cursor-pointer text-[10px] text-slate-400">View returned evidence and source details</summary>
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-[9px] leading-4 text-slate-400">{JSON.stringify(evidence, null, 2)}</pre>
                          </details>
                        </div>
                      )}
                    </div>
                  </motion.article>
                );
              })}
            </AnimatePresence>
          </div>
        </section>
      </div>
    </div>
  );
}

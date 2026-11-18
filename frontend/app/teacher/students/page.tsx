'use client';

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowDown, ArrowRight, ArrowUp, ClipboardCheck, Download, Eye, LayoutGrid, Search, SlidersHorizontal, Sparkles, Table2, UserRoundSearch, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading, RiskBadge } from '@/components/ui';
import { API_BASE, api, readToken } from '@/lib/api';
import { formatRiskProbability } from '@/lib/risk';

type Student = { id: number; student_id: string; full_name: string; program?: string; year?: string; department?: string; section?: string; risk_level: string; risk_probability: number; attendance: number; current_gpa: number; momentum_trend?: string; assignments_completed: number };
type DirectoryResponse = { items: Student[]; total: number };

export default function StudentsPage() {
  const [items, setItems] = useState<Student[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [risk, setRisk] = useState('');
  const [department, setDepartment] = useState('');
  const [section, setSection] = useState('');
  const [course, setCourse] = useState('');
  const [momentum, setMomentum] = useState('');
  const [attendanceBand, setAttendanceBand] = useState('');
  const [sort, setSort] = useState('full_name');
  const [descending, setDescending] = useState(false);
  const [page, setPage] = useState(0);
  const [error, setError] = useState('');
  const [view, setView] = useState<'cards' | 'table'>('cards');
  const [intelligenceStudent, setIntelligenceStudent] = useState<Student | null>(null);
  const reduceMotion = useReducedMotion() ?? false;
  const intelligenceTriggerRef = useRef<HTMLButtonElement | null>(null);
  const intelligenceCloseRef = useRef<HTMLButtonElement | null>(null);
  const intelligenceDrawerRef = useRef<HTMLElement | null>(null);
  const viewToggleRef = useRef<HTMLButtonElement | null>(null);
  const pageSize = 25;

  const query = useMemo(() => {
    const params = new URLSearchParams({ offset: String(page * pageSize), limit: String(pageSize), sort, direction: descending ? 'desc' : 'asc' });
    if (search.trim()) params.set('search', search.trim());
    if (risk) params.set('risk', risk);
    if (department) params.set('department', department);
    if (section) params.set('section', section);
    if (course) params.set('course', course);
    if (momentum) params.set('momentum', momentum);
    if (attendanceBand === 'low') params.set('attendance_max', '74.99');
    if (attendanceBand === 'watch') { params.set('attendance_min', '75'); params.set('attendance_max', '84.99'); }
    if (attendanceBand === 'steady') params.set('attendance_min', '85');
    return params.toString();
  }, [page, search, risk, department, section, course, momentum, attendanceBand, sort, descending]);

  useEffect(() => {
    api<DirectoryResponse>(`/api/v1/students?${query}`).then((result) => { setItems(result.items); setTotal(result.total); setError(''); }).catch((requestError: Error) => setError(requestError.message));
  }, [query]);

  useEffect(() => {
    if (!intelligenceStudent) return;
    const previousFocus = intelligenceTriggerRef.current ?? document.activeElement;
    const viewToggle = viewToggleRef.current;
    intelligenceCloseRef.current?.focus();
    return () => {
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
      else viewToggle?.focus();
    };
  }, [intelligenceStudent]);

  useEffect(() => {
    if (!intelligenceStudent) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIntelligenceStudent(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [intelligenceStudent]);

  const containDrawerFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return;
    const focusable = intelligenceDrawerRef.current?.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const focusIsOutside = !intelligenceDrawerRef.current?.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || focusIsOutside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || focusIsOutside)) {
      event.preventDefault();
      first.focus();
    }
  };

  const toggleSort = (key: string) => { if (sort === key) setDescending(!descending); else { setSort(key); setDescending(false); } };
  const exportCsv = async () => {
    const response = await fetch(`${API_BASE}/api/v1/reports/export.csv`, { headers: { Authorization: `Bearer ${readToken() ?? ''}` } });
    if (!response.ok) { setError('Could not export this report.'); return; }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob());
    link.download = 'student-risk-report.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return <div className="fade-up">
    <PageHeading eyebrow="Student directory · demo data" title="Students" description="Find the right student and understand the signals that changed." action={<div className="flex flex-wrap items-center gap-2">
      <div role="group" aria-label="Student directory view" className="inline-flex h-10 items-center rounded-lg border border-white/10 bg-white/[.025] p-1">
        <button ref={viewToggleRef} type="button" aria-pressed={view === 'cards'} onClick={() => setView('cards')} className={`inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs transition ${view === 'cards' ? 'bg-rose-500/15 text-rose-100' : 'text-slate-400 hover:text-white'}`}><LayoutGrid size={14} /> Cards</button>
        <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')} className={`inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs transition ${view === 'table' ? 'bg-rose-500/15 text-rose-100' : 'text-slate-400 hover:text-white'}`}><Table2 size={14} /> Table</button>
      </div>
      <button type="button" onClick={exportCsv} className="inline-flex h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs font-medium text-slate-200 hover:bg-white/5"><Download size={15} /> Export CSV</button>
    </div>} />
    <section aria-label="Student directory summary" className="mb-4 grid gap-3 sm:grid-cols-2">
      <Metric label="Student records" value={total} detail="Total records matching current filters" color="amber" />
      <Metric label="On this page" value={items.length} detail={`Page ${page + 1} · ${pageSize} records per page`} color="rose" />
    </section>
    <div className="surface rounded-xl p-4 sm:p-5">
      <p className="mb-3 text-xs text-slate-500">Risk is the classifier&apos;s most probable class; its probability is shown alongside it.</p>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <label className="relative min-w-52 flex-1"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" /><span className="sr-only">Search students</span><input value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="Search name or student ID" className="h-10 w-full rounded-lg border border-white/10 bg-[#0b1320] pl-9 pr-3 text-sm outline-none focus:border-cyan-200/50" /></label>
        <div className="flex flex-wrap items-center gap-2"><SlidersHorizontal size={15} className="mr-1 hidden text-slate-500 sm:block" />
          <select aria-label="Filter by risk" value={risk} onChange={(event) => { setRisk(event.target.value); setPage(0); }} className="h-10 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All risks</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select>
          <select aria-label="Filter by department" value={department} onChange={(event) => { setDepartment(event.target.value); setPage(0); }} className="h-10 max-w-40 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All departments</option>{['Engineering', 'Sciences', 'Humanities', 'Business'].map((value) => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter by course" value={course} onChange={(event) => { setCourse(event.target.value); setPage(0); }} className="h-10 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All courses</option><option value="AF-101-A">AF-101-A</option></select>
            <select aria-label="Filter by section" value={section} onChange={(event) => { setSection(event.target.value); setPage(0); }} className="h-10 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All sections</option>{['A', 'B', 'C', 'D'].map((value) => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter by momentum" value={momentum} onChange={(event) => { setMomentum(event.target.value); setPage(0); }} className="h-10 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All momentum</option>{['IMPROVING', 'STABLE', 'DECLINING', 'VOLATILE', 'INSUFFICIENT_DATA'].map((value) => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter by attendance" value={attendanceBand} onChange={(event) => { setAttendanceBand(event.target.value); setPage(0); }} className="h-10 rounded-lg border border-white/10 bg-[#0b1320] px-3 text-xs text-slate-300"><option value="">All attendance</option><option value="low">Below 75%</option><option value="watch">75–84%</option><option value="steady">85% and above</option></select>
        </div>
      </div>
      {error && <div className="mt-4"><ErrorNotice message={error} /></div>}
      {view === 'cards' ? <div className="mt-4 grid gap-3">
        {items.map((student, index) => {
          const initials = student.full_name.trim().split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase() || '?';
          return <motion.article
            key={student.id}
            initial={reduceMotion ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0 : .22, delay: reduceMotion ? 0 : Math.min(index * .025, .2) }}
            className="surface group relative overflow-hidden rounded-xl p-4 transition-colors hover:border-rose-300/20 sm:p-5"
          >
            <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-rose-400/70 via-rose-500/15 to-transparent opacity-50 transition-opacity group-hover:opacity-100" />
            <div className="flex items-start gap-3 sm:gap-4">
              <div aria-hidden="true" className="relative grid size-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-rose-300/20 bg-[#1b1518] text-sm font-semibold tracking-wide text-rose-100 shadow-[0_0_24px_rgba(225,29,50,.12)] sm:size-14">
                <span className="absolute -right-2 -top-2 size-9 rotate-45 rounded-xl border border-rose-300/20" />
                <span className="absolute -bottom-4 -left-2 size-9 -rotate-12 rounded-full border border-rose-300/20" />
                <span className="relative">{initials}</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-sm font-semibold text-white sm:text-base">{student.full_name}</h2>
                    <p className="mt-1 font-mono text-[10px] text-slate-500">{student.student_id}</p>
                    <p className="mt-1 text-[11px] text-slate-400">{student.department ?? student.program ?? '—'} <span className="text-slate-600">·</span> Year {student.year ?? '—'} <span className="text-slate-600">·</span> Section {student.section ?? '—'}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <RiskBadge level={student.risk_level} />
                    <span className="rounded-md border border-white/[.08] bg-white/[.025] px-2 py-1 text-[10px] text-slate-300">{formatRiskProbability(student.risk_level, student.risk_probability)}</span>
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3 border-t border-white/[.07] pt-3 sm:grid-cols-4 xl:grid-cols-5">
                  <div><dt className="text-[9px] uppercase tracking-[.12em] text-slate-500">Attendance</dt><dd className={`mt-1 text-xs font-medium ${student.attendance < 75 ? 'text-rose-200' : 'text-slate-200'}`}>{student.attendance.toFixed(0)}%</dd></div>
                  <div><dt className="text-[9px] uppercase tracking-[.12em] text-slate-500">Performance</dt><dd className="mt-1 text-xs font-medium text-slate-200">{student.current_gpa.toFixed(2)} GPA</dd></div>
                  <div><dt className="text-[9px] uppercase tracking-[.12em] text-slate-500">Learning momentum</dt><dd className={`mt-1 text-xs font-medium ${student.momentum_trend === 'DECLINING' ? 'text-rose-200' : student.momentum_trend === 'IMPROVING' ? 'text-emerald-200' : 'text-slate-200'}`}>{student.momentum_trend?.replace('_', ' ') ?? '—'}</dd></div>
                  <div><dt className="text-[9px] uppercase tracking-[.12em] text-slate-500">Assignment engagement</dt><dd className="mt-1 text-xs font-medium text-slate-200">{student.assignments_completed} completed</dd></div>
                  <div className="col-span-2 flex flex-wrap items-center gap-2 sm:col-span-4 xl:col-span-1 xl:justify-end">
                    <button type="button" onClick={(event) => { intelligenceTriggerRef.current = event.currentTarget; setIntelligenceStudent(student); }} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-rose-300/20 px-3 text-[10px] font-medium text-rose-100 transition hover:bg-rose-300/[.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"><Sparkles size={13} /> View intelligence</button>
                    <Link href={`/teacher/recovery/students/${student.id}`} aria-label={`Open recovery profile for ${student.full_name}`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-rose-600 px-3 text-[10px] font-medium text-white transition hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"><ClipboardCheck size={13} /> Recovery profile <ArrowRight size={12} /></Link>
                    <Link href={`/teacher/students/${student.id}`} aria-label={`View ${student.full_name}`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-[10px] font-medium text-slate-300 transition hover:border-white/20 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"><Eye size={13} /> Student 360</Link>
                  </div>
                </dl>
              </div>
            </div>
          </motion.article>;
        })}
      </div> : <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[1030px] text-left text-xs"><thead className="border-b border-white/8 text-[10px] uppercase tracking-[.1em] text-slate-500"><tr>
        {([['full_name', 'Student'], ['student_id', 'Student ID'], ['department', 'Program'], ['risk_level', 'Risk'], ['risk_probability', 'Class probability'], ['attendance', 'Attendance'], ['current_gpa', 'Performance'], ['momentum_trend', 'Momentum'], ['assignments_completed', 'Assignment engagement']] as const).map(([key, label]) => <th key={key} className="pb-3 pr-4 font-medium"><button type="button" onClick={() => toggleSort(key === 'student_id' || key === 'department' || key === 'momentum_trend' ? 'full_name' : key)} className="inline-flex items-center gap-1 hover:text-white">{label}{sort === key && (descending ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}</button></th>)}<th className="pb-3 text-right font-medium">Actions</th>
      </tr></thead><tbody>{items.map((student) => <tr key={student.id} className="border-b border-white/5 transition hover:bg-white/[.025]"><td className="py-3.5 pr-4"><Link href={`/teacher/students/${student.id}`} className="font-medium text-slate-100 hover:text-cyan-200">{student.full_name}</Link><div className="mt-1 text-[10px] text-slate-500">Year {student.year ?? '—'} · Section {student.section ?? '—'}</div></td><td className="py-3.5 pr-4 font-mono text-slate-400">{student.student_id}</td><td className="py-3.5 pr-4 text-slate-400">{student.department ?? student.program ?? '—'}</td><td className="py-3.5 pr-4"><RiskBadge level={student.risk_level} /></td><td className="py-3.5 pr-4 text-slate-300">{formatRiskProbability(student.risk_level, student.risk_probability)}</td><td className="py-3.5 pr-4"><span className={student.attendance < 75 ? 'text-rose-200' : 'text-slate-300'}>{student.attendance.toFixed(0)}%</span></td><td className="py-3.5 pr-4 text-slate-300">{student.current_gpa.toFixed(2)} GPA</td><td className="py-3.5 pr-4"><span className={student.momentum_trend === 'DECLINING' ? 'text-rose-200' : student.momentum_trend === 'IMPROVING' ? 'text-emerald-200' : 'text-slate-300'}>{student.momentum_trend?.replace('_', ' ') ?? '—'}</span></td><td className="py-3.5 pr-4 text-slate-300">{student.assignments_completed} completed</td><td className="py-3.5 text-right"><div className="inline-flex gap-2"><Link href={`/teacher/recovery/students/${student.id}`} aria-label={`Open recovery profile for ${student.full_name}`} className="inline-flex items-center gap-1 rounded-md bg-rose-600 px-2.5 py-1.5 text-[10px] text-white hover:bg-rose-500"><ClipboardCheck size={12} />Recovery</Link><Link href={`/teacher/students/${student.id}`} aria-label={`View ${student.full_name}`} className="inline-flex rounded-md border border-white/10 px-2.5 py-1.5 text-[10px] text-slate-300 hover:border-cyan-200/30 hover:text-cyan-100">Student 360</Link></div></td></tr>)}</tbody></table></div>}
      {!error && !items.length && <div className="py-8"><Empty title="No students match these filters" detail="Try adjusting the risk, attendance, or department filters." /></div>}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/8 pt-4"><div className="text-xs text-slate-500"><UserRoundSearch size={14} className="mr-1.5 inline" />{total.toLocaleString()} students · fictional dataset</div><div className="flex items-center gap-2"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300 disabled:opacity-40">Previous</button><span className="min-w-20 text-center text-xs text-slate-500">Page {page + 1} / {Math.max(1, Math.ceil(total / pageSize))}</span><button type="button" disabled={(page + 1) * pageSize >= total} onClick={() => setPage(page + 1)} className="rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300 disabled:opacity-40">Next</button></div></div>
    </div>
    <AnimatePresence>
      {intelligenceStudent && <motion.div className="fixed inset-0 z-[70] overflow-hidden" initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : .18 }}>
        <button type="button" aria-label={`Close ${intelligenceStudent.full_name} intelligence panel`} onClick={() => setIntelligenceStudent(null)} className="absolute inset-0 size-full bg-black/65 backdrop-blur-[2px]" />
        <motion.aside
          ref={intelligenceDrawerRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="student-intelligence-title"
          onKeyDown={containDrawerFocus}
          initial={reduceMotion ? false : { x: 440 }}
          animate={{ x: 0 }}
          exit={{ x: reduceMotion ? 0 : 440 }}
          transition={{ duration: reduceMotion ? 0 : .22, ease: 'easeOut' }}
          className="absolute inset-y-0 right-0 flex w-full max-w-[440px] flex-col border-l border-white/10 bg-[#111214] shadow-2xl"
        >
          <div className="flex items-start justify-between gap-4 border-b border-white/[.08] p-5 sm:p-6">
            <div><p className="eyebrow">Student intelligence · recorded evidence</p><h2 id="student-intelligence-title" className="mt-2 text-lg font-semibold text-white">{intelligenceStudent.full_name}</h2><p className="mt-1 font-mono text-[10px] text-slate-500">{intelligenceStudent.student_id}</p></div>
            <button ref={intelligenceCloseRef} type="button" onClick={() => setIntelligenceStudent(null)} aria-label="Close intelligence panel" className="grid size-9 shrink-0 place-items-center rounded-lg border border-white/10 text-slate-400 transition hover:bg-white/[.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"><X size={17} /></button>
          </div>
          <div className="flex-1 space-y-5 overflow-y-auto p-5 sm:p-6">
            <section className="rounded-xl border border-rose-300/15 bg-rose-300/[.035] p-4">
              <p className="text-[9px] uppercase tracking-[.15em] text-slate-500">Current model output</p>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><RiskBadge level={intelligenceStudent.risk_level} /><span className="text-sm font-semibold text-slate-100">{formatRiskProbability(intelligenceStudent.risk_level, intelligenceStudent.risk_probability)}</span></div>
              <p className="mt-3 text-[10px] leading-5 text-slate-500">The displayed probability belongs to the predicted class. This directory view does not include a feature-contribution explanation.</p>
            </section>
            <section>
              <h3 className="text-xs font-semibold text-slate-200">Academic evidence in this record</h3>
              <dl className="mt-3 divide-y divide-white/[.07] rounded-xl border border-white/[.07] px-4">
                <div className="flex items-center justify-between gap-4 py-3"><dt className="text-xs text-slate-400">Attendance</dt><dd className="text-xs font-medium text-slate-100">{intelligenceStudent.attendance.toFixed(0)}%</dd></div>
                <div className="flex items-center justify-between gap-4 py-3"><dt className="text-xs text-slate-400">Current GPA</dt><dd className="text-xs font-medium text-slate-100">{intelligenceStudent.current_gpa.toFixed(2)}</dd></div>
                <div className="flex items-center justify-between gap-4 py-3"><dt className="text-xs text-slate-400">Learning momentum</dt><dd className="text-xs font-medium text-slate-100">{intelligenceStudent.momentum_trend?.replace('_', ' ') ?? '—'}</dd></div>
                <div className="flex items-center justify-between gap-4 py-3"><dt className="text-xs text-slate-400">Completed assignments</dt><dd className="text-xs font-medium text-slate-100">{intelligenceStudent.assignments_completed}</dd></div>
              </dl>
              <p className="mt-3 text-[10px] leading-5 text-slate-500">These are the academic fields supplied with the current directory record; no additional history or causes are inferred here.</p>
            </section>
            <section className="rounded-xl border border-white/[.07] bg-white/[.02] p-4">
              <p className="text-[9px] uppercase tracking-[.15em] text-slate-500">Academic placement</p>
              <p className="mt-2 text-xs text-slate-200">{intelligenceStudent.department ?? intelligenceStudent.program ?? '—'}</p>
              <p className="mt-1 text-[10px] text-slate-500">Year {intelligenceStudent.year ?? '—'} · Section {intelligenceStudent.section ?? '—'}</p>
            </section>
          </div>
          <div className="border-t border-white/[.08] p-5 sm:p-6">
            <Link href={`/teacher/students/${intelligenceStudent.id}`} className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60"><Eye size={15} /> View full student profile</Link>
            <p className="mt-2 text-center text-[9px] text-slate-600">Opens the existing Student 360 profile.</p>
          </div>
        </motion.aside>
      </motion.div>}
    </AnimatePresence>
  </div>;
}
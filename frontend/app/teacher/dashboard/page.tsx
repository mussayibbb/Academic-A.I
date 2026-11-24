'use client';

import { motion, useReducedMotion } from 'framer-motion';
import {
  ArrowRight, CalendarClock, CheckCheck, ClipboardList, CircleDot, Clock3, Sparkles, UsersRound,
} from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useSpotlight } from '@/components/useSpotlight';
import { Empty, ErrorNotice, Metric, PageHeading, PanelTitle, RiskBadge } from '@/components/ui';
import { api, readCachedApiData } from '@/lib/api';
import { formatRiskProbability } from '@/lib/risk';

const AcademicSolarSystem = dynamic(() => import('@/components/AcademicSolarSystem'), {
  ssr: false,
});

type Student = { id: number; full_name: string; student_id: string; program?: string; department?: string; data_origin?: string; risk_level: string; risk_probability: number; risk_status?: string; attendance: number; current_gpa: number };
type Assignment = { id: number; title: string; subject: string; course?: string; due_date?: string | null };
type Dashboard = { teacher_name: string; risk_status?: string; risk_status_message?: string | null; summary: { students: number; assignments: number; submissions: number; pending_reviews: number; intervention_drafts: number; high_risk: number; medium_risk: number; low_risk: number; risk_scored_students: number }; students_needing_attention: Student[]; assignments: Assignment[]; upcoming_deadlines: Assignment[] };
type Submission = { id: number; status: string; assignment_id: number; submitted_at?: string | null; grade?: number | null };

const dayPart = () => {
  const hour = new Date().getHours();
  return hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
};

export default function TeacherDashboard() {
  const [data, setData] = useState<Dashboard | null>(() => readCachedApiData<Dashboard>('/api/v1/dashboard/teacher'));
  const [submissions, setSubmissions] = useState<Submission[]>(() => readCachedApiData<Submission[]>('/api/v1/submissions') ?? []);
  const [error, setError] = useState('');
  const reduceMotion = useReducedMotion();
  const spotlightHandlers = useSpotlight();

  useEffect(() => {
    Promise.all([api<Dashboard>('/api/v1/dashboard/teacher'), api<Submission[]>('/api/v1/submissions')])
      .then(([dashboard, recent]) => { setData(dashboard); setSubmissions(recent); })
      .catch((requestError: Error) => setError(requestError.message));
  }, []);

  if (error) return <ErrorNotice message={error} />;
  if (!data) return <div aria-label="Loading faculty dashboard" className="space-y-5"><div className="skeleton h-64 rounded-2xl" /><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[1, 2, 3, 4].map((item) => <div key={item} className="skeleton h-32 rounded-xl" />)}</div><div className="skeleton h-72 rounded-xl" /></div>;

  const attentionCount = data.summary.high_risk + data.summary.medium_risk;
  const totalStudents = data.summary.students;
  const scoredStudents = data.summary.risk_scored_students;
  const highShare = scoredStudents ? data.summary.high_risk / scoredStudents : 0;
  const mediumShare = scoredStudents ? data.summary.medium_risk / scoredStudents : 0;
  const lowShare = scoredStudents ? data.summary.low_risk / scoredStudents : 0;
  const priorities = [
    { name: 'Students', count: totalStudents, detail: 'in the academic workspace', icon: UsersRound, href: '/teacher/students', color: 'text-slate-100' },
    { name: 'Needs a closer look', count: attentionCount, detail: 'high or medium predicted risk', icon: CircleDot, href: '/teacher/early-warnings', color: 'text-rose-200' },
    { name: 'Reviews waiting', count: data.summary.pending_reviews, detail: 'submissions to review', icon: ClipboardList, href: '/teacher/assignments', color: 'text-slate-100' },
    { name: 'Drafts to review', count: data.summary.intervention_drafts, detail: 'AI-assisted · faculty approval', icon: Sparkles, href: '/teacher/interventions', color: 'text-slate-100' },
  ];

  return <div className="page-content space-y-6 sm:space-y-7">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <PageHeading eyebrow="Faculty workspace · student records" title={`${dayPart()}, ${data.teacher_name.split(' ')[1] ?? data.teacher_name}`} description="Start with current academic records and the model-supported signals that need review." />
      <span className="mb-7 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[.035] px-3 py-1.5 text-[9px] font-semibold tracking-[.12em] text-slate-400"><span aria-hidden="true" className="size-1.5 rounded-full bg-rose-400" /> {data.risk_status === 'unvalidated-demo-model' ? 'DEMO MODEL PREVIEW' : 'FACULTY WORKSPACE'}</span>
    </div>
    {data.risk_status_message && <p role="status" className="rounded-lg border border-amber-200/20 bg-amber-200/[.04] p-3 text-xs leading-5 text-amber-100">{data.risk_status_message}</p>}

    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: .45, ease: 'easeOut' }}
      aria-labelledby="dashboard-hero-title"
      {...spotlightHandlers}
      className="spotlight spotlight--hero relative isolate overflow-hidden rounded-2xl border border-white/[.09] bg-[#1b1d21] shadow-[0_32px_90px_rgba(0,0,0,.35)]"
    >
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_78%_52%,rgba(130,8,28,.28),transparent_34%),linear-gradient(120deg,rgba(255,255,255,.018),transparent_52%)]" />
      <div className="grid gap-5 px-5 py-7 sm:px-8 sm:py-9 lg:grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)] lg:items-center lg:gap-4 lg:px-9 lg:py-9">
        <div className="relative z-10">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-rose-300/20 bg-rose-300/[.055] px-3 py-1.5 text-[10px] font-medium tracking-wide text-rose-100"><Sparkles size={13} aria-hidden="true" /> Academic intelligence · human support</div>
          <h2 id="dashboard-hero-title" className="max-w-2xl text-3xl font-semibold leading-[1.05] tracking-[-.045em] text-white sm:text-4xl lg:text-[3.25rem]">Notice the signals.<br /><span className="text-rose-200">Make room to help.</span></h2>
          <p className="mt-4 max-w-xl text-sm leading-6 text-slate-400 sm:text-[15px]">A clearer view of academic activity helps you focus your time where a thoughtful check-in may matter most.</p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link href="/teacher/early-warnings" className="group inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white shadow-[0_8px_24px_rgba(225,29,50,.22)] transition hover:bg-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300"><span>Review student signals</span><ArrowRight size={15} className="transition-transform group-hover:translate-x-0.5" /></Link>
            <Link href="/teacher/students" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/[.13] bg-white/[.035] px-4 text-xs font-medium text-slate-200 transition hover:bg-white/[.07]">Open student directory</Link>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-white/[.08] pt-4 text-[10px] text-slate-500"><span className="inline-flex items-center gap-2"><span aria-hidden="true" className="size-1.5 rounded-full bg-rose-400" /> Signals are decision support</span><span>Faculty judgment comes first</span></div>
        </div>

        <motion.div
          initial={reduceMotion ? false : { opacity: 0, scale: .96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: .7, ease: 'easeOut', delay: .08 }}
          role="img"
          aria-label={`Current risk distribution: ${scoredStudents} scored students; ${data.summary.high_risk} high risk, ${data.summary.medium_risk} medium risk, ${data.summary.low_risk} low risk`}
          className="solar-system-visualization relative mx-auto w-full"
        >
          <AcademicSolarSystem
            totalStudents={scoredStudents}
            highRisk={data.summary.high_risk}
            mediumRisk={data.summary.medium_risk}
            lowRisk={data.summary.low_risk}
            reduceMotion={Boolean(reduceMotion)}
          />
          <div aria-hidden="true" className="solar-risk-card solar-risk-card--high">
            <div className="text-[9px] uppercase tracking-[.13em] text-rose-300">High risk</div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums text-rose-100">{data.summary.high_risk}</div>
          </div>
          <div aria-hidden="true" className="solar-risk-card solar-risk-card--medium">
            <div className="text-[9px] uppercase tracking-[.13em] text-slate-400">Medium risk</div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums text-slate-100">{data.summary.medium_risk}</div>
          </div>
          <div aria-hidden="true" className="solar-risk-card solar-risk-card--low">
            <div className="text-[9px] uppercase tracking-[.13em] text-slate-400">Low risk</div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums text-slate-100">{data.summary.low_risk}</div>
          </div>
        </motion.div>
      </div>
    </motion.section>

    <section aria-label="Faculty workspace metrics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {priorities.map((item, index) => <motion.div key={item.name} initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: reduceMotion ? 0 : index * .06, duration: .32 }} className="relative"><Metric label={item.name} value={item.count} detail={item.detail} color={item.name === 'Needs a closer look' ? 'rose' : 'amber'} href={item.href} spotlight /><span aria-hidden="true" className={`pointer-events-none absolute right-5 top-5 ${item.color}`}><item.icon size={16} /></span></motion.div>)}
    </section>

    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(290px,.75fr)]">
      <section {...spotlightHandlers} className="surface spotlight min-w-0 overflow-hidden rounded-xl">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-white/[.07] px-5 py-4"><div><p className="eyebrow">Faculty review · current records</p><h2 className="mt-1 text-lg font-semibold tracking-tight text-white">Students needing attention</h2></div><Link href="/teacher/students" className="text-xs text-rose-200 transition hover:text-white">Open directory <ArrowRight size={13} className="ml-1 inline" /></Link></div>
        <div className="hidden grid-cols-[minmax(150px,1.2fr)_minmax(90px,.65fr)_minmax(120px,.8fr)_minmax(130px,1fr)_40px] gap-4 px-5 py-3 text-[9px] font-medium uppercase tracking-[.15em] text-slate-600 sm:grid"><span>Student</span><span>Risk</span><span>Class confidence</span><span>Review signal</span><span /></div>
        {data.students_needing_attention.slice(0, 7).map((student, index) => <motion.div key={student.id} initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: reduceMotion ? 0 : index * .035 }} className="grid gap-2 border-t border-white/[.06] px-4 py-3.5 transition-colors hover:bg-white/[.025] sm:grid-cols-[minmax(150px,1.2fr)_minmax(90px,.65fr)_minmax(120px,.8fr)_minmax(130px,1fr)_40px] sm:items-center sm:gap-4 sm:px-5">
          <div className="min-w-0"><Link href={`/teacher/recovery/students/${student.id}`} className="block truncate text-xs font-medium text-slate-100 hover:text-rose-200">{student.full_name}</Link><div className="mt-1 truncate font-mono text-[9px] text-slate-600">{student.student_id}</div><div className="mt-1 text-[9px] text-slate-500">{student.data_origin ?? 'Source unavailable'}</div></div>
          <div className="flex items-center gap-2"><span className="text-[9px] text-slate-600 sm:hidden">Risk</span><RiskBadge level={student.risk_level} /></div>
          <div className="text-xs tabular-nums text-slate-300"><span className="mr-2 text-[9px] text-slate-600 sm:hidden">Confidence</span>{student.risk_status === 'unvalidated-demo-model' ? 'Unvalidated demo preview' : formatRiskProbability(student.risk_level, student.risk_probability)}</div>
          <div className="text-[11px] text-slate-500">{student.attendance < 75 ? 'Attendance below 75%' : 'Academic indicators to review'}</div>
          <Link aria-label={`Open recovery profile for ${student.full_name}`} href={`/teacher/recovery/students/${student.id}`} className="hidden justify-self-end text-slate-600 transition hover:text-rose-200 sm:block"><ArrowRight size={15} /></Link>
        </motion.div>)}
        {!data.students_needing_attention.length && <div className="p-5"><Empty title="No immediate student flags" detail="New signals will appear here as academic data is recorded." /></div>}
      </section>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
        <section {...spotlightHandlers} className="surface spotlight rounded-xl p-5">
          <PanelTitle title="Recorded risk distribution" href="/teacher/risk-monitoring" />
          <p className="-mt-2 mb-5 text-[10px] text-slate-500">Current model-supported classifications · {scoredStudents} of {totalStudents} student records</p>
          <div className="space-y-4">
            {([['High risk', data.summary.high_risk, highShare, 'bg-rose-500'], ['Medium risk', data.summary.medium_risk, mediumShare, 'bg-slate-400'], ['Low risk', data.summary.low_risk, lowShare, 'bg-slate-200']] as const).map(([label, count, share, color]) => <div key={label}><div className="mb-1.5 flex items-center justify-between text-[11px]"><span className="text-slate-400">{label}</span><span className="tabular-nums text-slate-300">{count}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-white/[.07]"><motion.div initial={reduceMotion ? false : { width: 0 }} animate={{ width: `${share * 100}%` }} transition={{ duration: .7, ease: 'easeOut' }} className={`h-full rounded-full ${color}`} /></div></div>)}
          </div>
          <p className="mt-5 border-t border-white/[.07] pt-3 text-[9px] leading-4 text-slate-600">Model classifications provide review signals only. They do not replace faculty judgment.</p>
        </section>

        <section {...spotlightHandlers} className="surface spotlight rounded-xl p-5">
          <PanelTitle title="Upcoming deadlines" href="/teacher/assignments" />
          {data.upcoming_deadlines.length ? <div className="space-y-1">{data.upcoming_deadlines.slice(0, 5).map((item) => <div key={item.id} className="flex items-start gap-3 border-t border-white/[.06] py-3 first:border-0"><span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-white/[.08] bg-white/[.035] text-slate-300"><CalendarClock size={15} /></span><div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-slate-200">{item.title}</div><div className="mt-1 text-[10px] text-slate-500">{item.course ?? item.subject} · {item.due_date ? new Date(item.due_date).toLocaleDateString() : 'No due date'}</div></div><Clock3 size={13} aria-hidden="true" className="mt-1 shrink-0 text-slate-600" /></div>)}</div> : <Empty title="No upcoming deadlines" detail="Published assignments will appear here." />}
        </section>
      </div>
    </div>

    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section {...spotlightHandlers} className="surface spotlight rounded-xl p-5">
        <PanelTitle title="Assignment activity" href="/teacher/assignments" />
        <div className="space-y-2">{data.assignments.slice(0, 5).map((assignment) => {
          const count = submissions.filter((item) => item.assignment_id === assignment.id).length;
          return <Link key={assignment.id} href={`/teacher/assignments/${assignment.id}/submissions`} className="flex min-h-14 items-center gap-3 rounded-lg border border-white/[.06] bg-black/10 px-3 py-3 transition hover:border-white/[.13] hover:bg-white/[.035]"><span className="grid size-8 place-items-center rounded-md border border-white/[.07] bg-white/[.035] text-slate-300"><CheckCheck size={15} /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-slate-200">{assignment.title}</span><span className="mt-1 block text-[10px] text-slate-500">{assignment.course ?? assignment.subject}</span></span><span className="text-[10px] tabular-nums text-slate-500">{count} received</span></Link>;
        })}{!data.assignments.length && <Empty title="No active assignments" detail="Create an assignment when the next class task is ready." />}</div>
      </section>

      <section {...spotlightHandlers} className="surface-light spotlight spotlight--light relative overflow-hidden rounded-xl p-5 sm:p-6">
        <div aria-hidden="true" className="pointer-events-none absolute -right-10 -top-12 size-44 rounded-full bg-[radial-gradient(circle,rgba(225,29,50,.12),transparent_70%)]" />
        <div className="relative"><p className="text-[9px] font-semibold uppercase tracking-[.17em] text-[#a80f24]">Human-centered insight</p><h2 className="mt-2 text-lg font-semibold tracking-tight text-[#111214]">A signal is a starting point.</h2><p className="mt-2 max-w-lg text-xs leading-5 text-[#60666e]">Use dated records and the student’s full context to decide whether a conversation or a support action is appropriate.</p><div className="mt-5 flex flex-wrap gap-3"><Link href="/teacher/risk-monitoring" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-[#111214] px-3.5 text-xs font-semibold text-white transition hover:bg-[#25282d]">Explore risk history <ArrowRight size={14} /></Link><span className="inline-flex items-center gap-2 text-[10px] text-[#747b85]"><span aria-hidden="true" className="size-1.5 rounded-full bg-[#e11d32]" />Review with care</span></div></div>
      </section>
    </div>
  </div>;
}

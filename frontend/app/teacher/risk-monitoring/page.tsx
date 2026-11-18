'use client';

import { ArrowDownRight, ArrowUpRight, Minus, TrendingUp } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Empty, ErrorNotice, Metric, PageHeading, RiskBadge } from '@/components/ui';
import { api } from '@/lib/api';

type MonitorData = {
  distribution: Record<string, number>;
  momentum: Record<string, number>;
  transitions: { student_id: number; name: string; from: string; to: string; recorded_at: string }[];
  history_note: string;
  history: { student_id: number; risk_level: string; recorded_at: string }[];
};

export default function RiskMonitoringPage() {
  const [data, setData] = useState<MonitorData | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api<MonitorData>('/api/v1/risk/monitoring').then(setData).catch((requestError: Error) => setError(requestError.message));
  }, []);

  const trend = useMemo(() => {
    if (!data) return [];
    const weeks = new Map<string, { date: string; HIGH: number; MEDIUM: number; LOW: number }>();
    for (const item of data.history) {
      const week = new Date(item.recorded_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      const row = weeks.get(week) ?? { date: week, HIGH: 0, MEDIUM: 0, LOW: 0 };
      const level = item.risk_level as 'HIGH' | 'MEDIUM' | 'LOW';
      row[level] = (row[level] ?? 0) + 1;
      weeks.set(week, row);
    }
    return Array.from(weeks.values()).slice(-12);
  }, [data]);

  if (error) return <ErrorNotice message={error} />;
  if (!data) return <div aria-label="Loading risk history" className="space-y-4"><div className="skeleton h-28 rounded-xl" /><div className="skeleton h-80 rounded-xl" /></div>;

  return <div className="page-content">
    <PageHeading eyebrow="Risk monitoring · dated records" title="Risk, in motion" description="Track recorded changes between levels and review the context behind each snapshot." />

    <section aria-label="Current risk classifications" className="grid gap-3 sm:grid-cols-3">
      <Metric label="High risk" value={data.distribution.HIGH ?? 0} detail="Current student classifications" color="rose" />
      <Metric label="Medium risk" value={data.distribution.MEDIUM ?? 0} detail="Current student classifications" color="amber" />
      <Metric label="Low risk" value={data.distribution.LOW ?? 0} detail="Current student classifications" color="mint" />
    </section>

    <section className="surface mt-4 rounded-xl p-5 sm:p-6">
      <p className="eyebrow">Academic momentum · dated assessments</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(['IMPROVING', 'STABLE', 'DECLINING', 'VOLATILE'] as const).map((level) => <div key={level} className="rounded-lg border border-white/[.07] bg-white/[.025] p-3"><div className="text-[9px] font-medium tracking-[.12em] text-slate-500">{level}</div><div className="mt-1 text-xl font-semibold tabular-nums text-slate-100">{data.momentum[level] ?? 0}</div></div>)}
      </div>
      <p className="mt-3 text-[10px] leading-5 text-slate-500">Students with fewer than four observations: {data.momentum.INSUFFICIENT_DATA ?? 0}. Trends are not classified before the minimum history is met.</p>
    </section>

    <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(300px,.85fr)]">
      <section aria-labelledby="snapshot-heading" className="surface min-w-0 rounded-xl p-5 sm:p-6">
        <p className="eyebrow">Historical records</p><h2 id="snapshot-heading" className="mt-1 text-base font-semibold">Recorded risk snapshots</h2><p className="mt-1 text-xs leading-5 text-slate-500">{data.history_note}</p>
        {trend.length ? <div role="img" aria-label="Stacked bar chart of dated student risk snapshots" className="mt-4 h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={trend}>
              <CartesianGrid stroke="rgba(255,255,255,.08)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: '#25282d', border: '1px solid #454a52', borderRadius: 8, color: '#f7f8fa', fontSize: 11 }} />
              <Bar dataKey="HIGH" stackId="risk" fill="#e11d32" radius={[3, 3, 0, 0]} />
              <Bar dataKey="MEDIUM" stackId="risk" fill="#747b85" />
              <Bar dataKey="LOW" stackId="risk" fill="#e6e8eb" />
            </BarChart>
          </ResponsiveContainer>
        </div> : <div className="mt-5"><Empty title="No risk snapshots yet" detail="Risk history appears once model predictions are saved." /></div>}
        <div className="mt-2 flex flex-wrap gap-4 text-[10px] text-slate-400"><span><i aria-hidden="true" className="mr-1.5 inline-block size-1.5 rounded-full bg-rose-500" />High</span><span><i aria-hidden="true" className="mr-1.5 inline-block size-1.5 rounded-full bg-slate-500" />Medium</span><span><i aria-hidden="true" className="mr-1.5 inline-block size-1.5 rounded-full bg-slate-200" />Low</span></div>
      </section>

      <section aria-labelledby="transitions-heading" className="surface rounded-xl p-5 sm:p-6">
        <p className="eyebrow">Consecutive snapshots</p><h2 id="transitions-heading" className="mt-1 text-base font-semibold">Risk transitions</h2><p className="mt-1 text-xs text-slate-500">Changes between recorded snapshots.</p>
        <div className="mt-4 divide-y divide-white/[.07]">
          {data.transitions.map((item, index) => <article key={`${item.student_id}-${item.recorded_at}-${index}`} className="flex items-center gap-3 py-3 first:pt-0">
            <span aria-hidden="true" className={`grid size-8 shrink-0 place-items-center rounded-lg border ${item.to === 'HIGH' ? 'border-rose-300/20 bg-rose-300/[.08] text-rose-200' : 'border-white/[.08] bg-white/[.035] text-slate-300'}`}>{item.to === 'HIGH' ? <ArrowUpRight size={15} /> : item.to === 'LOW' ? <ArrowDownRight size={15} /> : <Minus size={15} />}</span>
            <div className="min-w-0 flex-1"><Link href={`/teacher/students/${item.student_id}`} className="block truncate text-xs font-medium text-slate-200 hover:text-rose-200">{item.name}</Link><div className="mt-1 text-[9px] text-slate-500">{new Date(item.recorded_at).toLocaleString()}</div></div>
            <div className="flex shrink-0 items-center gap-1"><RiskBadge level={item.from} /><span aria-hidden="true" className="text-slate-600">→</span><RiskBadge level={item.to} /></div>
          </article>)}
          {!data.transitions.length && <div className="py-6"><Empty title="No level transitions" detail="New snapshots will make changes visible here." /></div>}
        </div>
        <div className="mt-3 flex items-center gap-2 border-t border-white/[.07] pt-3 text-[10px] text-slate-500"><TrendingUp size={13} className="text-rose-200" />Transitions are descriptive records, not proof of intervention impact.</div>
      </section>
    </div>
  </div>;
}

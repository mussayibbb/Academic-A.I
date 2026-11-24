'use client';

import { Activity, ArrowRight, Info } from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading, RiskBadge } from '@/components/ui';
import { api, readCachedApiData } from '@/lib/api';

const TeacherFeatureImportanceChart = dynamic(() => import('@/components/charts/TeacherFeatureImportanceChart'), {
  ssr: false,
  loading: () => <div className="skeleton h-full w-full rounded-lg" aria-hidden="true" />,
});

type Dashboard = { summary: { students: number; high_risk: number; medium_risk: number; low_risk: number } };
type Importance = { features: { name: string; importance: number }[] };
type Monitor = { transitions: { name: string; from: string; to: string; recorded_at: string }[] };

export default function TeacherAnalyticsPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(() => readCachedApiData<Dashboard>('/api/v1/dashboard/teacher'));
  const [importance, setImportance] = useState<Importance | null>(() => readCachedApiData<Importance>('/api/v1/risk/feature-importance'));
  const [monitor, setMonitor] = useState<Monitor | null>(() => readCachedApiData<Monitor>('/api/v1/risk/monitoring'));
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      api<Dashboard>('/api/v1/dashboard/teacher'),
      api<Monitor>('/api/v1/risk/monitoring'),
    ]).then(async ([summary, risk]) => {
      setDashboard(summary);
      setMonitor(risk);
      setImportance(await api<Importance>('/api/v1/risk/feature-importance'));
    }).catch((requestError: Error) => setError(requestError.message));
  }, []);

  if (error) return <ErrorNotice message={error} />;
  if (!dashboard || !importance || !monitor) return <div aria-label="Loading analytics" className="space-y-4"><div className="skeleton h-32 rounded-xl" /><div className="skeleton h-96 rounded-xl" /></div>;

  const elevated = dashboard.summary.high_risk + dashboard.summary.medium_risk;
  return <div className="page-content">
    <PageHeading eyebrow="Academic signals · demonstration model" title="Analytics" description="Explainable indicators help faculty decide where to look more closely." action={<Link href="/teacher/risk-monitoring" className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 transition hover:border-rose-300/30 hover:text-white">Risk history <ArrowRight size={14} /></Link>} />

    <section aria-label="Analytics summary" className="mb-4 grid gap-3 sm:grid-cols-3">
      <Metric label="Students represented" value={dashboard.summary.students} detail="Current saved student records" color="amber" />
      <Metric label="Elevated classifications" value={elevated} detail="High or medium · review signals" color="rose" />
      <Metric label="Recorded transitions" value={monitor.transitions.length} detail="Between consecutive snapshots" color="amber" />
    </section>

    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(300px,.9fr)]">
      <section aria-labelledby="importance-heading" className="surface min-w-0 rounded-xl p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <div><p className="eyebrow">Model explainability</p><h2 id="importance-heading" className="mt-1 text-base font-semibold text-white">Global feature contribution</h2><p className="mt-1 text-xs text-slate-500">Mean absolute SHAP value from the trained model.</p></div>
          <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-rose-300/15 bg-rose-300/[.05] text-rose-200"><Activity size={16} /></span>
        </div>
        {importance.features.length ? <div role="img" aria-label="Bar chart of global model feature contribution" className="mt-5 h-[340px]">
          <TeacherFeatureImportanceChart data={importance.features} />
        </div> : <div className="py-8"><Empty title="Feature contributions unavailable" detail="No model feature contribution values were returned." /></div>}
        <div className="mt-3 flex gap-2 rounded-lg border border-white/[.08] bg-white/[.025] p-3 text-[10px] leading-5 text-slate-400"><Info size={13} className="mt-0.5 shrink-0 text-rose-200" />Feature contributions describe this trained demonstration model. They are not causal findings or validated institutional thresholds.</div>
      </section>

      <section aria-labelledby="transitions-heading" className="surface rounded-xl p-5 sm:p-6">
        <p className="eyebrow">Recorded snapshots</p><h2 id="transitions-heading" className="mt-1 text-base font-semibold text-white">Recent risk transitions</h2><p className="mt-1 text-xs text-slate-500">Snapshot-to-snapshot changes for faculty review.</p>
        <div className="mt-4 divide-y divide-white/[.07]">
          {monitor.transitions.slice(-12).reverse().map((item, index) => <div key={`${item.name}-${index}`} className="flex items-center gap-3 py-3 first:pt-0">
            <div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-slate-200">{item.name}</div><div className="mt-1 text-[10px] text-slate-500">{new Date(item.recorded_at).toLocaleDateString()}</div></div>
            <div className="flex shrink-0 items-center gap-1"><RiskBadge level={item.from} /><span aria-hidden="true" className="text-slate-600">→</span><RiskBadge level={item.to} /></div>
          </div>)}
          {!monitor.transitions.length && <div className="py-6"><Empty title="No risk transitions yet" detail="Changes will appear when consecutive risk snapshots are recorded." /></div>}
        </div>
        <Link href="/teacher/risk-monitoring" className="mt-3 inline-flex min-h-10 items-center gap-2 border-t border-white/[.07] pt-3 text-xs text-rose-200 transition hover:text-white">Explore risk monitoring <ArrowRight size={14} /></Link>
      </section>
    </div>
  </div>;
}

'use client';

import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useReducedMotion } from 'framer-motion';
import { useSpotlight } from '@/components/useSpotlight';

export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description?: string; action?: React.ReactNode }) {
  return <div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">{eyebrow}</p><h1 className="mt-2 text-3xl font-semibold tracking-[-.035em] text-white sm:text-[2.1rem]">{title}</h1>{description && <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">{description}</p>}</div>{action}</div>;
}

export function RiskBadge({ level }: { level: string }) {
  const style = level === 'HIGH' ? 'border-rose-300/30 bg-rose-300/10 text-rose-200' : level === 'MEDIUM' ? 'border-white/15 bg-white/5 text-slate-200' : 'border-white/10 bg-white/[.025] text-slate-400';
  return <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-[.08em] ${style}`}><span aria-hidden="true" className="size-1.5 rounded-full bg-current" />{level}</span>;
}

export function Metric({ label, value, detail, color = 'cyan', href, spotlight = false }: { label: string; value: string | number; detail: string; color?: 'cyan' | 'rose' | 'amber' | 'mint'; href?: string; spotlight?: boolean }) {
  const [displayValue, setDisplayValue] = useState<string | number>(typeof value === 'number' ? 0 : value);
  const reduceMotion = useReducedMotion();
  const spotlightHandlers = useSpotlight();

  useEffect(() => {
    if (typeof value !== 'number' || reduceMotion) {
      setDisplayValue(value);
      return;
    }

    let frame = 0;
    const startedAt = performance.now();
    const duration = 680;
    const update = (now: number) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - (1 - progress) ** 3;
      setDisplayValue(progress === 1 ? value : Math.round(value * eased * 100) / 100);
      if (progress < 1) frame = requestAnimationFrame(update);
    };

    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [reduceMotion, value]);

  const colors = { cyan: 'text-rose-200', rose: 'text-rose-200', amber: 'text-slate-100', mint: 'text-slate-100' };
  const content = <div {...(spotlight ? spotlightHandlers : {})} className={`surface metric-card group relative min-h-32 overflow-hidden rounded-xl p-4 sm:p-5${spotlight ? ' spotlight' : ''}`}><div className="flex items-start justify-between"><span className="text-xs font-medium text-slate-400">{label}</span>{href && <ArrowUpRight size={14} className="text-slate-600 transition group-hover:text-rose-200" />}</div><div aria-label={`${label}: ${value}`} className={`mt-4 text-3xl font-semibold tabular-nums ${colors[color]}`}>{displayValue}</div><div className="mt-1 text-[11px] text-slate-500">{detail}</div></div>;
  return href ? <Link href={href}>{content}</Link> : content;
}

export function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="rounded-lg border border-dashed border-white/10 px-5 py-8 text-center"><div className="text-sm font-medium text-slate-300">{title}</div><p className="mt-1 text-xs text-slate-500">{detail}</p></div>;
}

export function ErrorNotice({ message }: { message: string }) {
  return <div role="alert" className="whitespace-pre-line rounded-lg border border-rose-300/25 bg-rose-300/[.06] px-4 py-3 text-sm text-rose-100">{message}</div>;
}

export function PanelTitle({ title, href, linkLabel = 'View all' }: { title: string; href?: string; linkLabel?: string }) {
  return <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold text-slate-100">{title}</h2>{href && <Link href={href} className="text-xs text-rose-200 transition-colors hover:text-white">{linkLabel}</Link>}</div>;
}
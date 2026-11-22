'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import EarlyWarningRiskTrajectory from '@/components/EarlyWarningRiskTrajectory';
import { api } from '@/lib/api';

type Priority = 'High' | 'Medium' | 'Low';
type RiskClass = 'Normal' | 'Watch' | 'At risk';

type Summary = {
  signals: number;
  highPriority: number;
  watchList: number;
  recoveredThisMonth: number;
  modelRefreshedAt: string;
};

type WarningItem = {
  studentId: number;
  name: string;
  department: string | null;
  riskProbability: number;
  riskClass: RiskClass;
  priority: Priority;
  topFactor: string;
};

type WarningList = {
  summary: Summary;
  items: WarningItem[];
  nextPage: number | null;
};

type Factor = {
  key: string;
  label: string;
  detail: string;
  currentValue: number;
  previousValue: number;
  unit: string;
  contributionShare: number;
};

type WarningDetail = {
  studentId: number;
  name: string;
  department: string | null;
  riskProbability: number;
  riskClass: RiskClass;
  priority: Priority;
  confidence: number;
  signalsUsed: number;
  weekChange: number;
  insufficientData: boolean;
  trend: { weekStart: string; risk: number }[];
  forecast: { noAction: number; withPlan: number };
  factors: Factor[];
  precautions: { factorKey: string; text: string }[];
  actions: { type: string; label: string; at: string; by: number | null }[];
};

type SimulationResult = { riskProbability: number; delta: number };

const priorityColor = (priority: Priority) =>
  priority === 'High' ? 'var(--hi)' : priority === 'Medium' ? 'var(--md)' : 'var(--lo)';

const supportedRecoveryFactors = new Set([
  'attendance', 'assessment', 'missing_submissions', 'activity_per_week',
  'attendance_change', 'assessment_change', 'momentum_score', 'current_gpa',
]);

function PredictedRisk({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setShown(value);
      shownRef.current = value;
      return;
    }
    let frame = 0;
    const startValue = shownRef.current;
    const startAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - startAt) / 400);
      const current = startValue + (value - startValue) * progress;
      shownRef.current = current;
      setShown(current);
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{Math.round(shown * 100)}%</>;
}

function CountUp({ value }: { value: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(value);
      return;
    }
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / 900);
      setShown(Math.round(value * (1 - Math.pow(1 - progress, 3))));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{shown}</>;
}

export default function EarlyWarningsPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [items, setItems] = useState<WarningItem[]>([]);
  const [nextPage, setNextPage] = useState<number | null>(1);
  const [severity, setSeverity] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<WarningDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [simulationError, setSimulationError] = useState('');
  const [simulationLoading, setSimulationLoading] = useState(false);
  const [toast, setToast] = useState('');
  const [composer, setComposer] = useState<number | null>(null);
  const [composerValue, setComposerValue] = useState('');
  const [composerDate, setComposerDate] = useState('');
  const [actions, setActions] = useState<WarningDetail['actions']>([]);
  const [simulations, setSimulations] = useState<Record<string, number>>({});
  const [simulationResult, setSimulationResult] = useState<SimulationResult | null>(null);
  const rowsRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef(0);
  const simTimer = useRef<ReturnType<typeof setTimeout>>();
  const simRequestRef = useRef(0);
  const simAbortRef = useRef<AbortController | null>(null);
  const [animateIn, setAnimateIn] = useState(false);

  useEffect(() => {
    setAnimateIn(false);
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setAnimateIn(true);
      return;
    }
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => setAnimateIn(true));
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [selectedId, detail]);

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 2200);
  };

  const loadList = useCallback(async (page: number, replace = false) => {
    const requestId = ++requestRef.current;
    if (page === 1) setLoading(true);
    else setLoadingMore(true);
    setError('');
    try {
      const params = new URLSearchParams({ page: String(page), pageSize: '50', days: '30' });
      if (severity) params.set('severity', severity);
      if (query.trim()) params.set('q', query.trim());
      const data = await api<WarningList>(`/api/v1/early-warnings?${params}`);
      if (requestId !== requestRef.current) return;
      setSummary(data.summary);
      setItems((current) => replace ? data.items : [...current, ...data.items]);
      setNextPage(data.nextPage);
      if (data.items.length) setSelectedId((current) => current ?? data.items[0].studentId);
    } catch (requestError) {
      if (requestId === requestRef.current) setError((requestError as Error).message);
    } finally {
      if (requestId === requestRef.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [query, severity]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setItems([]);
      setNextPage(1);
      void loadList(1, true);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [severity, query, loadList]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    setDetailLoading(true);
    setDetailError('');
    setSimulationError('');
    setSimulationLoading(false);
    setDetail(null);
    setComposer(null);
    setSimulationResult(null);
    setSimulations({});
    simAbortRef.current?.abort();
    if (simTimer.current) clearTimeout(simTimer.current);
    api<WarningDetail>(`/api/v1/early-warnings/${selectedId}`)
      .then((data) => {
        if (!active) return;
        setDetail(data);
        setActions(data.actions);
        setSimulationResult({ riskProbability: data.riskProbability, delta: 0 });
        setSimulations(Object.fromEntries(data.factors.slice(0, 3)
          .filter((factor) => supportedRecoveryFactors.has(factor.key))
          .map((factor) => [factor.key, 0])));
      })
      .catch((requestError: Error) => {
        if (active) setDetailError(requestError.message);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    const element = rowsRef.current;
    if (!element) return;
    const onScroll = () => {
      if (loadingMore || !nextPage) return;
      if (element.scrollTop + element.clientHeight >= element.scrollHeight - 100) void loadList(nextPage);
    };
    element.addEventListener('scroll', onScroll);
    return () => element.removeEventListener('scroll', onScroll);
  }, [nextPage, loadingMore, loadList]);

  const runSimulation = (values: Record<string, number>) => {
    if (!selectedId) return;
    if (simTimer.current) clearTimeout(simTimer.current);
    simAbortRef.current?.abort();
    const requestId = ++simRequestRef.current;
    setSimulationError('');
    setSimulationLoading(true);
    simTimer.current = setTimeout(() => {
      const controller = new AbortController();
      simAbortRef.current = controller;
      const recoveries = Object.fromEntries(
        Object.entries(values).map(([feature, value]) => [feature, value / 100]),
      );
      api<{ riskProbability: number; riskClass: RiskClass; delta: number }>(
        `/api/v1/early-warnings/${selectedId}/simulate`,
        { method: 'POST', body: JSON.stringify({ recoveries }), signal: controller.signal },
      ).then((result) => {
        if (requestId === simRequestRef.current) {
          setSimulationResult({ riskProbability: result.riskProbability, delta: result.delta });
          setSimulationError('');
        }
      }).catch((requestError: Error) => {
        if (requestId === simRequestRef.current && requestError.name !== 'AbortError') {
          setSimulationError(requestError.message || 'Could not update the simulation.');
        }
      }).finally(() => {
        if (requestId === simRequestRef.current) setSimulationLoading(false);
      });
    }, 150);
  };

  const performAction = async (
    actionIndex: number,
  ) => {
    if (!detail) return;
    const previous = actions;
    const type = actionIndex === 0 ? 'meeting' : actionIndex === 1 ? 'task' : actionIndex === 2 ? 'mentor_alert' : 'subject_recovery';
    let path = '';
    let body: Record<string, unknown> = { studentId: detail.studentId };
    if (actionIndex === 0) {
      path = '/api/v1/interventions/meetings';
      body = { ...body, slotStart: composerDate || new Date(Date.now() + 86400000).toISOString(), durationMin: 30, note: composerValue };
    } else if (actionIndex === 1) {
      path = '/api/v1/interventions/tasks';
      body = { ...body, taskType: composerValue || 'Catch-up task', dueAt: composerDate || new Date(Date.now() + 7 * 86400000).toISOString(), note: composerValue };
    } else if (actionIndex === 2) {
      path = '/api/v1/interventions/mentor-alert';
      body = { ...body, message: composerValue || 'Please review this student’s current early-warning signals.' };
    } else {
      path = '/api/v1/subject-recovery/enrol';
    }
    const optimistic = [...previous, {
      type,
      label: ['Host a meeting', 'Assign a recovery task', 'Alert class mentor', 'Add to Subject recovery'][actionIndex],
      at: new Date().toISOString(),
      by: null,
    }];
    setActions(optimistic);
    setComposer(null);
    try {
      const saved = await api<{ type: string; label: string; at: string; by: number | null }>(path, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setActions([...previous, saved]);
      showToast(`${saved.label} saved`);
    } catch (requestError) {
      setActions(previous);
      showToast((requestError as Error).message);
    }
  };

  const openComposer = (index: number) => {
    setComposer(composer === index ? null : index);
    setComposerValue('');
    setComposerDate('');
  };

  const riskProbability = detail ? Math.round(detail.riskProbability * 100) : 0;
  const riskColor = detail ? priorityColor(detail.priority) : 'var(--lo)';

  return (
    <div className="ew-page">
      <style>{`
        .ew-page{--ew-bg:#0e0d10;--ew-panel:#17151a;--ew-panel2:#1f1c23;--ew-line:#2c2831;--ew-tx:#f4f1f5;--ew-mute:#a49eab;--hi:#ff3b5c;--md:#ffb020;--lo:#3ddc97;min-width:0;color:var(--ew-tx)}
        .ew-page h1,.ew-page h2,.ew-page h3{font-family:"Bricolage Grotesque","DM Sans",sans-serif;letter-spacing:-.02em}
        .ew-top{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}.ew-top h1{font-size:clamp(30px,4vw,46px);line-height:1}.ew-sub{color:var(--ew-mute);margin:8px 0 0;max-width:56ch}
        .ew-live{display:flex;align-items:center;gap:8px;color:var(--ew-mute);font-size:13px;border:1px solid var(--ew-line);padding:7px 12px;border-radius:99px}.ew-live i{width:8px;height:8px;border-radius:50%;background:var(--lo);animation:ewPulse 1.8s infinite}
        .ew-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:26px 0 18px}.ew-stat{background:var(--ew-panel);border:1px solid var(--ew-line);border-radius:16px;padding:16px 18px}.ew-stat b{font:800 34px "Bricolage Grotesque",sans-serif;display:block;line-height:1.1}.ew-stat span{color:var(--ew-mute);font-size:13px}.ew-stat.high b{color:var(--hi)}.ew-stat.watch b{color:var(--md)}
        .ew-split{display:grid;grid-template-columns:minmax(300px,380px) 1fr;gap:18px;align-items:start}.ew-list{background:var(--ew-panel);border:1px solid var(--ew-line);border-radius:18px;padding:12px;position:sticky;top:12px}.ew-tools{display:flex;gap:8px;margin:2px 2px 10px;flex-wrap:wrap}.ew-search{height:34px;min-width:150px;flex:1;border:1px solid var(--ew-line);border-radius:99px;background:var(--ew-panel2);color:var(--ew-tx);padding:0 12px}.ew-chip{border:1px solid var(--ew-line);background:none;color:var(--ew-mute);padding:6px 11px;border-radius:99px;font-size:13px}.ew-chip.on{background:var(--ew-tx);color:var(--ew-bg);border-color:var(--ew-tx)}
        .ew-rows{display:flex;flex-direction:column;gap:6px;max-height:calc(100vh - 220px);overflow:auto}.ew-row{display:grid;grid-template-columns:44px 1fr auto;gap:12px;align-items:center;text-align:left;width:100%;border:1px solid transparent;background:var(--ew-panel2);color:inherit;padding:10px 12px;border-radius:13px;cursor:pointer;transition:.2s;animation:ewIn .5s both}.ew-row:hover{transform:translateX(3px)}.ew-row.on{border-color:var(--row-c);box-shadow:0 0 0 3px color-mix(in srgb,var(--row-c) 18%,transparent)}
        .ew-pct{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;font:700 13px "Bricolage Grotesque",sans-serif;background:conic-gradient(var(--row-c) calc(var(--row-p)*1%),var(--ew-line) 0);position:relative}.ew-pct:after{content:"";position:absolute;inset:4px;background:var(--ew-panel2);border-radius:50%}.ew-pct span{position:relative;z-index:1}.ew-row.high .ew-pct{animation:ewRing 2s infinite}.ew-row strong{display:block;font-size:14px}.ew-row small{color:var(--ew-mute);font-size:12px}.ew-tag{font-size:11px;font-weight:700;padding:3px 9px;border-radius:99px;color:var(--row-c);background:color-mix(in srgb,var(--row-c) 15%,transparent)}
        .ew-detail{display:flex;flex-direction:column;gap:16px;min-width:0}.ew-card{background:var(--ew-panel);border:1px solid var(--ew-line);border-radius:18px;padding:22px}.ew-card h3{font-size:19px;margin:0 0 4px}.ew-lead{color:var(--ew-mute);margin:0 0 16px;font-size:14px}.ew-hero{display:grid;grid-template-columns:170px 1fr;gap:22px;align-items:center;animation:ewIn .45s both}.ew-gauge{position:relative;width:170px;height:170px}.ew-gauge svg{transform:rotate(-90deg)}.ew-gauge div{position:absolute;inset:0;display:grid;place-content:center;text-align:center}.ew-gauge b{font:800 40px "Bricolage Grotesque",sans-serif;line-height:1}.ew-gauge span{font-size:12px;color:var(--ew-mute)}.ew-hero.high{animation:ewIn .45s both,ewGlow 2.6s 1s infinite}.ew-hero h2{font-size:26px;margin:0}.ew-meta{color:var(--ew-mute);font-size:14px;margin:4px 0 14px}.ew-chipline{display:flex;gap:8px;flex-wrap:wrap;margin:-4px 0 12px}.ew-chipline span{font-size:12px;color:var(--ew-mute);border:1px solid var(--ew-line);border-radius:99px;padding:3px 10px}.ew-chipline b{color:var(--ew-tx)}
        .ew-zone{height:10px;border-radius:9px;background:linear-gradient(90deg,var(--lo),var(--md) 55%,var(--hi));position:relative;margin:8px 0 6px}.ew-zone i{position:absolute;top:-5px;width:4px;height:20px;border-radius:3px;background:var(--ew-tx);left:calc(var(--risk)*1% - 2px);transition:left 1.2s cubic-bezier(.2,.8,.2,1);box-shadow:0 0 0 3px var(--ew-bg)}.ew-zl{display:flex;justify-content:space-between;font-size:12px;color:var(--ew-mute)}
        .ew-param{display:grid;grid-template-columns:200px 1fr 70px;gap:14px;align-items:center;padding:11px 0;border-top:1px solid var(--ew-line);animation:ewIn .5s both}.ew-param:first-of-type{border-top:0}.ew-param b{font-size:14px;display:block}.ew-param small{color:var(--ew-mute);font-size:12px}.ew-bar{height:9px;background:var(--ew-panel2);border-radius:9px;overflow:hidden}.ew-bar i{display:block;height:100%;border-radius:9px;background:linear-gradient(90deg,var(--md),var(--hi));transition:width 1s cubic-bezier(.2,.8,.2,1)}.ew-param em{font-style:normal;font-weight:700;text-align:right;color:var(--hi)}
        .ew-prec{display:grid;gap:10px}.ew-prec div{display:grid;grid-template-columns:28px 1fr;gap:12px;padding:12px 14px;background:var(--ew-panel2);border-radius:12px;font-size:14px;animation:ewIn .45s both}.ew-proj{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.ew-proj div{padding:12px 14px;border-radius:12px;background:var(--ew-panel2);font-size:13px;color:var(--ew-mute)}.ew-proj b{display:block;font:800 26px "Bricolage Grotesque",sans-serif}
        .ew-sl{display:grid;grid-template-columns:200px 1fr 46px;gap:14px;align-items:center;padding:8px 0;font-size:14px}.ew-sl input{accent-color:var(--hi);width:100%}.ew-sres{display:flex;flex-direction:column;align-items:stretch;gap:6px;margin-top:14px;padding:14px;border-radius:14px;background:var(--ew-panel2)}.ew-sres-row{display:flex;align-items:center;gap:16px}.ew-sres b{font:800 34px "Bricolage Grotesque",sans-serif;min-width:80px;transition:color .3s}.ew-sres .ew-bar{flex:1}.ew-sres .ew-bar i{transition:width .5s,background-color .3s}.ew-simline{color:var(--ew-mute);font-size:12px}.ew-simerror{color:var(--ew-mute);font-size:12px;margin:8px 0 0}.ew-simerror button{border:0;background:none;color:var(--ew-tx);text-decoration:underline;padding:0;cursor:pointer}
        .ew-acts{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.ew-act{text-align:left;background:var(--ew-panel2);border:1px solid var(--ew-line);color:inherit;border-radius:14px;padding:14px;cursor:pointer;transition:.2s}.ew-act:hover{transform:translateY(-3px);border-color:var(--hi)}.ew-act.pri{background:var(--hi);border-color:var(--hi);color:#fff}.ew-act b{display:block}.ew-act small{color:var(--ew-mute)}.ew-act.pri small{color:rgba(255,255,255,.85)}
        .ew-composer{display:none;margin-top:14px}.ew-composer.open{display:block;animation:ewExpand .35s both}.ew-composer-box{border:1px dashed var(--hi);border-radius:14px;padding:14px}.ew-composer input{width:100%;margin:8px 0;border:1px solid var(--ew-line);background:var(--ew-panel2);color:var(--ew-tx);border-radius:8px;padding:9px}.ew-go{background:var(--hi);color:#fff;border:0;border-radius:10px;padding:10px 16px;font-weight:700}
        .ew-log{margin:14px 0 0;padding:0;list-style:none;font-size:13px;color:var(--ew-mute)}.ew-log li{padding:6px 0;border-top:1px solid var(--ew-line)}.ew-toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,40px);opacity:0;background:var(--ew-tx);color:var(--ew-bg);padding:11px 18px;border-radius:12px;font-weight:700;z-index:50;transition:.35s}.ew-toast.show{transform:translate(-50%,0);opacity:1}.ew-note{color:var(--ew-mute);font-size:12px;margin-top:12px}
        .ew-tr-card svg{display:block;width:100%;height:auto;overflow:visible}.ew-trend-line{fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;stroke-dashoffset:1;transition:stroke-dashoffset 1.5s cubic-bezier(.4,0,.2,1)}.ew-trend-line.animate{stroke-dashoffset:0}.ew-area{opacity:0;transition:opacity 1s .6s}.ew-area.animate{opacity:1}.ew-forecast{fill:none;stroke-width:2.5;stroke-dasharray:5 5;opacity:0;transition:opacity .8s 1.4s}.ew-forecast.animate{opacity:1}.ew-forecast-red{stroke:var(--hi)}.ew-forecast-green{stroke:var(--lo)}.ew-now-dot{transform-box:fill-box;transform-origin:center}.ew-now-dot.animate{animation:ewBlink 2s infinite}.ew-history-note{color:var(--ew-mute);font-size:12px;margin:6px 0}.ew-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;color:var(--ew-mute);font-size:12px}.ew-legend span{display:inline-flex;align-items:center;gap:6px}.ew-legend i{width:10px;height:10px;border-radius:50%}.ew-legend-red{background:var(--hi)}.ew-legend-green{background:var(--lo)}
        @keyframes ewIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}@keyframes ewPulse{0%{box-shadow:0 0 0 0 rgba(61,220,151,.6)}100%{box-shadow:0 0 0 10px rgba(61,220,151,0)}}@keyframes ewRing{0%{box-shadow:0 0 0 0 rgba(255,59,92,.5)}100%{box-shadow:0 0 0 9px rgba(255,59,92,0)}}@keyframes ewGlow{0%,100%{box-shadow:0 0 0 0 rgba(255,59,92,0)}50%{box-shadow:0 0 38px 0 rgba(255,59,92,.28)}}@keyframes ewExpand{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}@keyframes ewBlink{50%{transform:scale(1.9);opacity:.35}}
        @media(max-width:980px){.ew-split{grid-template-columns:1fr}.ew-list{position:static}.ew-rows{max-height:340px}.ew-stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
        @media(max-width:560px){.ew-hero{grid-template-columns:1fr;justify-items:center;text-align:center}.ew-param{grid-template-columns:1fr 60px}.ew-param .ew-bar{grid-column:1/-1;order:3}.ew-sl{grid-template-columns:1fr 46px}.ew-sl input{grid-column:1/-1;order:3}.ew-stats{grid-template-columns:1fr 1fr}.ew-proj{grid-template-columns:1fr}}
        @media(prefers-reduced-motion:reduce){.ew-page *{animation:none!important;transition:none!important}}
      `}</style>

      <div className="ew-top">
        <div><h1>Early warning center</h1><p className="ew-sub">Students are ranked by the model’s predicted chance of entering the danger zone. Select one to see why, and what to do next.</p></div>
        {summary && <div className="ew-live"><i />Model refreshed {new Date(summary.modelRefreshedAt).toLocaleString()}</div>}
      </div>

      <section className="ew-stats" aria-label="Summary">
        <div className="ew-stat"><b><CountUp value={summary?.signals ?? 0} /></b><span>Signals detected</span></div>
        <div className="ew-stat high"><b><CountUp value={summary?.highPriority ?? 0} /></b><span>High-priority students</span></div>
        <div className="ew-stat watch"><b><CountUp value={summary?.watchList ?? 0} /></b><span>On the watch list</span></div>
        <div className="ew-stat"><b><CountUp value={summary?.recoveredThisMonth ?? 0} /></b><span>Recovered this month</span></div>
      </section>

      {error && <div className="ew-card"><strong>Could not load early warnings</strong><p className="ew-lead">{error}</p><button className="ew-chip" onClick={() => loadList(1, true)}>Retry</button></div>}

      <div className="ew-split">
        <aside className="ew-list">
          <div className="ew-tools">
            {['All', 'High', 'Medium', 'Low'].map((filter) => <button key={filter} className={`ew-chip ${(!severity && filter === 'All') || severity === filter ? 'on' : ''}`} onClick={() => setSeverity(filter === 'All' ? '' : filter)}>{filter}</button>)}
            <input className="ew-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name" aria-label="Search students by name" />
          </div>
          <div className="ew-rows" ref={rowsRef} role="listbox" aria-label="Students by priority">
            {loading && !items.length ? [0, 1, 2, 3].map((item) => <div key={item} className="skeleton h-16 rounded-xl" />) : items.map((item, index) => {
              const color = priorityColor(item.priority);
              return <button
                key={item.studentId}
                role="option"
                aria-selected={item.studentId === selectedId}
                className={`ew-row ${item.priority.toLowerCase()} ${item.studentId === selectedId ? 'on' : ''}`}
                style={{ '--row-c': color, '--row-p': Math.round(item.riskProbability * 100), animationDelay: `${index * 70}ms` } as React.CSSProperties}
                onClick={() => setSelectedId(item.studentId)}
              >
                <span className="ew-pct"><span>{Math.round(item.riskProbability * 100)}%</span></span>
                <span><strong>{item.name}</strong><small>{item.department ?? 'Department not set'} · {item.topFactor}</small></span>
                <span className="ew-tag">{item.priority}</span>
              </button>;
            })}
            {!loading && !items.length && <p className="ew-lead p-3">No students in this group.</p>}
            {loadingMore && <div className="skeleton h-14 rounded-xl" />}
          </div>
        </aside>

        <section className="ew-detail" aria-live="polite" aria-busy={detailLoading}>
          {detailLoading && <div className="skeleton h-64 rounded-2xl" />}
          {detailError && <div className="ew-card"><strong>Could not load student detail</strong><p className="ew-lead">{detailError}</p><button className="ew-chip" onClick={() => selectedId && setSelectedId(selectedId)}>Retry</button></div>}
          {detail && <>
            {detail.insufficientData && <div className="ew-card"><strong>Not enough data yet</strong><p className="ew-lead">This student has fewer than 3 usable signals, so a risk ranking is not shown.</p></div>}
            <div className={`ew-card ew-hero ${detail.priority === 'High' ? 'high' : ''}`} style={{ '--risk': riskProbability } as React.CSSProperties}>
              <div className="ew-gauge">
                <svg width="170" height="170" viewBox="0 0 130 130" aria-hidden="true">
                  <circle cx="65" cy="65" r="54" fill="none" stroke="var(--ew-line)" strokeWidth="10" />
                  <circle cx="65" cy="65" r="54" fill="none" stroke={riskColor} strokeWidth="10" strokeLinecap="round" strokeDasharray={339.292} strokeDashoffset={339.292 * (1 - riskProbability / 100)} style={{ transition: 'stroke-dashoffset 1.2s cubic-bezier(.2,.8,.2,1)' }} />
                </svg>
                <div><b>{riskProbability}%</b><span>chance of danger zone</span></div>
              </div>
              <div><h2>{detail.name}</h2><div className="ew-meta">{detail.department ?? 'Department not set'} · {detail.priority} priority · predicted class: {detail.riskClass}</div>
                <div className="ew-chipline"><span>Model confidence <b>{Math.round(detail.confidence * 100)}%</b></span><span>This week <b>{detail.weekChange >= 0 ? '▲' : '▼'} {Math.abs(detail.weekChange)} pts</b></span><span>{detail.signalsUsed} signals used</span></div>
                <div className="ew-zone"><i /></div><div className="ew-zl"><span>Normal</span><span>Watch</span><span>Danger</span></div>
              </div>
            </div>

            <EarlyWarningRiskTrajectory trend={detail.trend} forecast={detail.forecast} currentRisk={detail.riskProbability} animateIn={animateIn} />

            <div className="ew-card">
              <h3>Why the model flagged this student</h3><p className="ew-lead">The factors that contribute most to this student’s predicted risk.</p>
              {detail.factors.length ? detail.factors.map((factor, index) => <div className="ew-param" key={factor.key} style={{ animationDelay: `${index * 80}ms` }}>
                <div><b>{factor.label}</b><small>{factor.detail}</small></div>
                <div className="ew-bar"><i style={{ width: `${factor.contributionShare * 100}%` }} /></div>
                <em>+{Math.round(factor.contributionShare * detail.riskProbability * 100)} pts</em>
              </div>) : <p className="ew-lead">Not enough data yet to explain individual factors.</p>}
            </div>

            <div className="ew-card">
              <h3>Precautions to bring them back to normal</h3><p className="ew-lead">Start with the factors that add the most risk.</p>
              <div className="ew-prec">{detail.precautions.map((precaution, index) => <div key={precaution.factorKey} style={{ animationDelay: `${index * 120}ms` }}><span aria-hidden="true">✓</span><span>{precaution.text}</span></div>)}</div>
              <div className="ew-proj"><div>In 14 days with no action<b style={{ color: 'var(--hi)' }}>{Math.round(detail.forecast.noAction * 100)}%</b></div><div>In 14 days if this plan is followed<b style={{ color: 'var(--lo)' }}>{Math.round(detail.forecast.withPlan * 100)}%</b></div></div>
            </div>

            <div className="ew-card">
              <h3>What-if simulator</h3><p className="ew-lead">Live changes are scored by the real model.</p>
              {detail.factors.slice(0, 3).filter((factor) => supportedRecoveryFactors.has(factor.key)).map((factor) => {
                const value = simulations[factor.key] ?? 0;
                return <div className="ew-sl" key={factor.key}>
                  <label htmlFor={`recovery-${factor.key}`}>{factor.label} recovers</label>
                  <input id={`recovery-${factor.key}`} type="range" min="0" max="100" value={value} onChange={(event) => {
                    const next = { ...simulations, [factor.key]: Number(event.target.value) };
                    setSimulations(next);
                    runSimulation(next);
                  }} />
                  <b>{Math.round(value)}%</b>
                </div>;
              })}
              {simulationResult !== null && detail && (() => {
                const score = Math.round(simulationResult.riskProbability * 100);
                const color = score >= 75 ? 'var(--hi)' : score >= 40 ? 'var(--md)' : 'var(--lo)';
                const tag = score >= 75 ? 'Danger' : score >= 40 ? 'Watch' : 'Normal';
                return <div className="ew-sres" aria-live="polite" style={{ opacity: simulationLoading ? 0.55 : 1 }}>
                  <span className="ew-simline">Predicted risk</span>
                  <div className="ew-sres-row"><b style={{ color }}><PredictedRisk value={simulationResult.riskProbability} /></b><div className="ew-bar"><i style={{ width: `${score}%`, backgroundColor: color }} /></div><span className="ew-tag" style={{ '--row-c': color } as React.CSSProperties}>{tag}</span></div>
                  <div className="ew-simline">Currently {Math.round(detail.riskProbability * 100)}%. This scenario changes it by {simulationResult.delta > 0 ? '+' : ''}{Math.round(simulationResult.delta)} pts.</div>
                </div>;
              })()}
              {simulationLoading && <p className="ew-simline" role="status">Updating prediction…</p>}
              {simulationError && <p className="ew-simerror" role="status">{simulationError} <button type="button" onClick={() => runSimulation(simulations)}>Retry</button></p>}
            </div>

            <div className="ew-card">
              <h3>Suggested actions</h3><p className="ew-lead">Choose one to act now. Each action is saved to the student’s record.</p>
              <div className="ew-acts">{[
                ['Host a meeting', 'Book a one-to-one check-in'],
                ['Assign a recovery task', 'Set a small catch-up task'],
                ['Alert class mentor', 'Share this signal with the mentor'],
                ['Add to Subject recovery', 'Enrol in the recovery plan'],
              ].map((action, index) => <button key={action[0]} className={`ew-act ${index === 0 ? 'pri' : ''}`} onClick={() => index < 2 ? openComposer(index) : void performAction(index)}><b>{action[0]}</b><small>{action[1]}</small></button>)}</div>
              {composer !== null && <div className={`ew-composer ${composer !== null ? 'open' : ''}`}><div className="ew-composer-box">
                {composer === 0 ? <><strong>Schedule a meeting</strong><input type="datetime-local" value={composerDate} onChange={(event) => setComposerDate(event.target.value)} /><input value={composerValue} onChange={(event) => setComposerValue(event.target.value)} placeholder="Meeting note" /></> : <><strong>Assign a recovery task</strong><input value={composerValue} onChange={(event) => setComposerValue(event.target.value)} placeholder="Task or check-in" /><input type="datetime-local" value={composerDate} onChange={(event) => setComposerDate(event.target.value)} /></>}
                <button className="ew-go" onClick={() => void performAction(composer)}>Confirm</button>
              </div></div>}
              {actions.length > 0 && <ul className="ew-log">{actions.map((action, index) => <li key={`${action.type}-${action.at}-${index}`}>{action.label} · {new Date(action.at).toLocaleString()}</li>)}</ul>}
              <p className="ew-note">Signals support faculty judgment. They are not a final decision about any student.</p>
            </div>
          </>}
        </section>
      </div>
      <div className={`ew-toast ${toast ? 'show' : ''}`} role="status">{toast}</div>
    </div>
  );
}

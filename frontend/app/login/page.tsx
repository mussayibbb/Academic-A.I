'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { ArrowRight, BookOpenCheck, LockKeyhole } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { API_BASE } from '@/lib/api';
import { parseLoginResponse } from '@/lib/login';

export default function LoginPage() {
  const [email, setEmail] = useState('teacher@example.com');
  const [password, setPassword] = useState('teacher123');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const router = useRouter();
  const reduceMotion = useReducedMotion() ?? false;

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await parseLoginResponse(response);
      window.localStorage.setItem('token', data.access_token);
      router.push(data.user.role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to reach the Academic AI API.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative grid min-h-screen overflow-hidden lg:grid-cols-[1.05fr_.95fr]">
      <section className="relative isolate flex min-h-[42vh] flex-col justify-between overflow-hidden border-b border-white/8 px-6 py-8 sm:px-12 lg:min-h-screen lg:border-b-0 lg:border-r lg:border-white/8 lg:px-16 lg:py-12">
        <div aria-hidden="true" className="pointer-events-none absolute -right-32 top-1/4 -z-10 size-[30rem] rounded-full border border-white/[.04]"><span className="absolute inset-8 rounded-full border border-rose-300/[.12]" /><span className="absolute inset-20 rounded-full border border-white/[.04]" /><span className="ambient-orb absolute right-[18%] top-[18%] size-3 rounded-full bg-rose-400 shadow-[0_0_32px_rgba(255,51,71,.7)]" /><span className="absolute bottom-[14%] left-[20%] size-1 rounded-full bg-white/60" /></div>
        <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-lg border border-rose-300/25 bg-rose-300/[.08] text-rose-200"><BookOpenCheck size={20} /></span><div className="text-sm font-semibold">Academic AI <span className="font-normal text-slate-500">for Impact</span></div></div>
        <div className="fade-up max-w-2xl py-12">
          <p className="eyebrow">Student support, made timely</p>
          <h1 className="mt-5 max-w-xl text-4xl font-semibold leading-[1.04] tracking-[-.045em] text-white sm:text-6xl">Notice the change.<br /><span className="text-rose-200">Make room to help.</span></h1>
          <p className="mt-6 max-w-lg text-base leading-7 text-slate-400">A shared academic workspace for educators and students, built around early signals and thoughtful action.</p>
          <div className="mt-12 grid max-w-xl grid-cols-3 border-y border-white/10 py-5">
            <div><div className="text-xl font-semibold text-white">01</div><div className="mt-1 text-xs text-slate-500">Observe</div></div>
            <div className="border-l border-white/10 pl-5"><div className="text-xl font-semibold text-white">02</div><div className="mt-1 text-xs text-slate-500">Understand</div></div>
            <div className="border-l border-white/10 pl-5"><div className="text-xl font-semibold text-white">03</div><div className="mt-1 text-xs text-slate-500">Support</div></div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-600"><LockKeyhole size={13} /> Private academic workspace</div>
      </section>

      <section className="auth-panel relative flex items-center justify-center bg-[#f7f8fa] px-5 py-12 text-[#111214] sm:px-10 lg:px-14">
        <motion.div initial={reduceMotion ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : .4 }} className="w-full max-w-[410px]">
          <p className="eyebrow">Welcome back</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-.035em] text-[#111214]">Sign in to continue</h2>
          <p className="mt-2 text-sm text-[#60666e]">Use your faculty or student account.</p>
          <form onSubmit={onSubmit} className="mt-8 space-y-5">
            <div><label htmlFor="email" className="mb-2 block text-xs font-semibold text-[#454a52]">Email address</label><input id="email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} className="h-12 w-full rounded-lg border border-[#d1d4d8] bg-white px-4 text-sm text-[#111214] outline-none transition focus:border-[#e11d32] focus:ring-2 focus:ring-[#e11d32]/10" /></div>
            <div><label htmlFor="password" className="mb-2 block text-xs font-semibold text-[#454a52]">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} className="h-12 w-full rounded-lg border border-[#d1d4d8] bg-white px-4 text-sm text-[#111214] outline-none transition focus:border-[#e11d32] focus:ring-2 focus:ring-[#e11d32]/10" /></div>
            {error && <p role="alert" className="rounded-lg border border-[#e11d32]/20 bg-[#fff0f2] px-3 py-2 text-sm text-[#a80f24]">{error}</p>}
            <button disabled={loading} className="group flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-[#e11d32] px-4 font-semibold text-white transition hover:bg-[#a80f24] disabled:cursor-wait disabled:opacity-60">{loading ? 'Signing in...' : 'Continue'}{!loading && <ArrowRight size={16} className="transition-transform group-hover:translate-x-1" />}</button>
          </form>
          <div className="mt-7 rounded-lg border border-[#d1d4d8] bg-white p-4">
            <div className="eyebrow">Demo data account</div>
            <div className="mt-3 grid grid-cols-[72px_1fr] gap-y-2 text-xs"><span className="text-[#60666e]">Faculty</span><span className="break-all font-mono text-[#303338]">teacher@example.com / teacher123</span><span className="text-[#60666e]">Student</span><span className="break-all font-mono text-[#303338]">student@example.com / student123</span></div>
          </div>
          <p className="mt-5 text-center text-[10px] text-[#60666e]">Fictional demonstration accounts; do not use real student data.</p>
        </motion.div>
      </section>
    </main>
  );
}
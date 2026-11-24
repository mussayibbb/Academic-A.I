'use client';

import { Fraunces } from 'next/font/google';
import { Instrument_Sans } from 'next/font/google';
import { useRouter } from 'next/navigation';
import { useEffect, useRef } from 'react';
import { api, clearApiCache } from '@/lib/api';
import ConstellationCanvas from './ConstellationCanvas';
import styles from './marketing.module.css';

const fraunces = Fraunces({
  subsets: ['latin'],
  variable: '--font-fraunces',
  display: 'swap',
});

const instrumentSans = Instrument_Sans({
  subsets: ['latin'],
  variable: '--font-instrument-sans',
  display: 'swap',
});

type CurrentUser = { role: string };

export default function MarketingShell({
  children,
  showProgress = false,
}: {
  children: React.ReactNode;
  showProgress?: boolean;
}) {
  const router = useRouter();
  const progressRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!window.localStorage.getItem('token')) return;

    let active = true;
    void api<CurrentUser>('/api/v1/me')
      .then((current) => {
        if (active) {
          router.replace(current.role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard');
        }
      })
      .catch(() => {
        if (active) {
          clearApiCache();
          window.localStorage.removeItem('token');
        }
      });

    return () => {
      active = false;
    };
  }, [router]);

  useEffect(() => {
    if (!showProgress) return;
    const updateProgress = () => {
      const documentHeight = document.documentElement.scrollHeight - window.innerHeight;
      const progress = documentHeight > 0 ? window.scrollY / documentHeight : 0;
      if (progressRef.current) progressRef.current.style.width = `${Math.min(100, Math.max(0, progress * 100))}%`;
    };
    updateProgress();
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', updateProgress);
    return () => {
      window.removeEventListener('scroll', updateProgress);
      window.removeEventListener('resize', updateProgress);
    };
  }, [showProgress]);

  return (
    <div className={`${styles.shell} ${fraunces.variable} ${instrumentSans.variable}`}>
      <ConstellationCanvas />
      {showProgress && <div ref={progressRef} className={styles.progress} aria-hidden="true" />}
      <div className={styles.content}>{children}</div>
    </div>
  );
}

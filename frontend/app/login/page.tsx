'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import MarketingShell from '@/components/marketing/MarketingShell';
import {
  DEMO_ACCOUNTS,
  DEMO_PLAN,
  DEMO_STUDENTS,
  type DemoRole,
} from '@/components/marketing/demo-data';
import { API_BASE } from '@/lib/api';
import { parseLoginResponse } from '@/lib/login';
import styles from '@/components/marketing/marketing.module.css';

type PreviewStyle = React.CSSProperties & {
  '--progress': string;
  '--progress-color': string;
};

function studentRiskLabel(risk: number) {
  return risk >= 70 ? 'At risk' : risk >= 40 ? 'Needs attention' : 'On track';
}

export default function LoginPage() {
  const [selectedRole, setSelectedRole] = useState<DemoRole>('faculty');
  const [email, setEmail] = useState(DEMO_ACCOUNTS.faculty.email);
  const [password, setPassword] = useState(DEMO_ACCOUNTS.faculty.password);
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const router = useRouter();

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

  const chooseRole = (role: DemoRole) => {
    setSelectedRole(role);
    setEmail(DEMO_ACCOUNTS[role].email);
    setPassword(DEMO_ACCOUNTS[role].password);
    setError('');
  };

  return (
    <MarketingShell>
      <main className={styles.loginPage}>
        <Link className={styles.back} href="/">← Back to home</Link>
        <div className={styles.loginCard} data-role={selectedRole}>
          <aside className={styles.preview} aria-label="Sample workspace preview">
            <span className={styles.previewLabel}>Sample data</span>
            <div id="faculty-preview" className={styles.previewPane} role="tabpanel" aria-labelledby="faculty-tab" data-active={selectedRole === 'faculty'} aria-hidden={selectedRole !== 'faculty'}>
              <h3>Faculty workspace</h3>
              <p>See who needs attention before the next assessment.</p>
              {DEMO_STUDENTS.slice(0, 3).map((student) => {
                const color = student.risk >= 70 ? 'var(--red)' : student.risk >= 40 ? '#ffb020' : 'var(--mint)';
                const style: PreviewStyle = {
                  '--progress': `${student.risk}%`,
                  '--progress-color': color,
                };
                return (
                  <div className={styles.previewRow} key={student.name}>
                    <b>{student.name}</b>
                    <em style={{ color }}>{studentRiskLabel(student.risk)}</em>
                    <span className={styles.previewTrack}><i style={style} /></span>
                  </div>
                );
              })}
            </div>
            <div id="student-preview" className={styles.previewPane} role="tabpanel" aria-labelledby="student-tab" data-active={selectedRole === 'student'} aria-hidden={selectedRole !== 'student'}>
              <h3>Student workspace</h3>
              <p>Your support plan, shared by your teacher.</p>
              {DEMO_PLAN.map((item) => {
                const color = item.status === 'Up next' ? 'var(--blue)' : 'var(--mint)';
                const style: PreviewStyle = {
                  '--progress': `${item.progress}%`,
                  '--progress-color': color,
                };
                return (
                  <div className={styles.previewRow} key={item.title}>
                    <b>{item.title}</b>
                    <em style={{ color }}>{item.status}</em>
                    <span className={styles.previewTrack}><i style={style} /></span>
                  </div>
                );
              })}
            </div>
          </aside>

          <form className={styles.loginForm} onSubmit={onSubmit}>
            <div className={styles.loginLogo}><i aria-hidden="true">A</i>Academic AI for Impact</div>
            <h1 className={styles.loginTitle}>Welcome back</h1>
            <p className={styles.loginSubtitle}>Choose how you&apos;re signing in.</p>
            <div className={styles.roleTabs} role="tablist" aria-label="Choose account type">
              <span className={styles.tabHighlight} aria-hidden="true" />
              <button
                type="button"
                role="tab"
                id="faculty-tab"
                aria-controls="faculty-preview"
                aria-selected={selectedRole === 'faculty'}
                onClick={() => chooseRole('faculty')}
              >
                Faculty
              </button>
              <button
                type="button"
                role="tab"
                id="student-tab"
                aria-controls="student-preview"
                aria-selected={selectedRole === 'student'}
                onClick={() => chooseRole('student')}
              >
                Student
              </button>
            </div>
            <div className={styles.field}>
              <input
                id="email"
                type="email"
                placeholder=" "
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <label htmlFor="email">Email address</label>
            </div>
            <div className={styles.field}>
              <input
                className={styles.passwordInput}
                id="password"
                type={showPassword ? 'text' : 'password'}
                placeholder=" "
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <label htmlFor="password">Password</label>
              <button
                className={styles.passwordToggle}
                type="button"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
            <button className={styles.continueButton} type="submit" disabled={loading}>
              {loading ? 'Signing in...' : 'Continue'}
            </button>
            <p className={`${styles.message} ${error ? styles.messageError : ''}`} aria-live="polite" aria-atomic="true">
              {error}
            </p>
            <div className={styles.demoAccount}>
              <strong>Demo data account</strong>
              <code>{DEMO_ACCOUNTS[selectedRole].email} / {DEMO_ACCOUNTS[selectedRole].password}</code>
            </div>
            <small className={styles.loginNote}>Fictional demonstration accounts; do not use real student data.</small>
          </form>
        </div>
      </main>
    </MarketingShell>
  );
}

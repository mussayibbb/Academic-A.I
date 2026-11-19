'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import MarketingShell from '@/components/marketing/MarketingShell';
import {
  DEMO_CLASS_RISKS,
  DEMO_STUDENTS,
  DEMO_SUBJECTS,
} from '@/components/marketing/demo-data';
import styles from './landing.module.css';

export default function LandingPage() {
  const pageRef = useRef<HTMLDivElement>(null);
  const [shownStudents, setShownStudents] = useState(0);
  const [scores, setScores] = useState<number[]>(DEMO_STUDENTS.map(() => 0));
  const [alertVisible, setAlertVisible] = useState(false);
  const [reviewedStudent, setReviewedStudent] = useState<number | null>(null);
  const [signalStudent, setSignalStudent] = useState<number | null>(null);
  const [activeStep, setActiveStep] = useState(-1);
  const [gain, setGain] = useState('0.0');
  const timerIds = useRef<number[]>([]);
  const animationFrame = useRef<number | null>(null);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const clearTimers = () => {
      timerIds.current.forEach((timer) => window.clearTimeout(timer));
      timerIds.current = [];
    };

    if (reducedMotion) {
      setShownStudents(DEMO_STUDENTS.length);
      setScores(DEMO_STUDENTS.map((student, index) => index === 1 ? 76 : student.risk));
      setAlertVisible(true);
      setSignalStudent(1);
      return clearTimers;
    }

    const later = (callback: () => void, delay: number) => {
      timerIds.current.push(window.setTimeout(callback, delay));
    };
    const runCycle = () => {
      clearTimers();
      setShownStudents(0);
      setScores(DEMO_STUDENTS.map(() => 0));
      setAlertVisible(false);
      setReviewedStudent(null);
      setSignalStudent(null);
      DEMO_STUDENTS.forEach((student, index) => {
        later(() => {
          setShownStudents((current) => Math.max(current, index + 1));
          setScores((current) => current.map((score, itemIndex) => itemIndex === index ? student.risk : score));
        }, 700 + index * 450);
      });
      later(() => setAlertVisible(true), 3600);
      later(() => {
        setScores((current) => current.map((score, index) => index === 1 ? 76 : score));
        setSignalStudent(1);
      }, 5000);
      later(runCycle, 11500);
    };
    runCycle();

    return clearTimers;
  }, []);

  useEffect(() => {
    const root = pageRef.current;
    if (!root) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const reveals = root.querySelectorAll<HTMLElement>(`.${styles.reveal}`);
    const fillRiskBars = (element: HTMLElement) => {
      element.querySelectorAll<HTMLElement>(`[data-risk-fill]`).forEach((bar) => {
        bar.style.width = `${bar.dataset.riskFill ?? '0'}%`;
      });
    };
    const animateGain = () => {
      if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
      if (reducedMotion) {
        setGain('3.0');
        return;
      }
      const start = performance.now();
      const tick = (now: number) => {
        const progress = Math.min(1, (now - start) / 1600);
        setGain((3 * progress).toFixed(1));
        if (progress < 1) animationFrame.current = window.requestAnimationFrame(tick);
      };
      animationFrame.current = window.requestAnimationFrame(tick);
    };

    if (!('IntersectionObserver' in window)) {
      reveals.forEach((element) => {
        element.classList.add(styles.visible);
        fillRiskBars(element);
        if (element.dataset.gainCard !== undefined) animateGain();
      });
      setActiveStep(3);
      return () => {
        if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
      };
    }

    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting || !(entry.target instanceof HTMLElement)) return;
        const element = entry.target;
        element.classList.add(styles.visible);
        fillRiskBars(element);
        if (element.dataset.gainCard !== undefined) animateGain();
        revealObserver.unobserve(element);
      });
    }, { threshold: 0.25 });
    reveals.forEach((element) => revealObserver.observe(element));

    const steps = root.querySelector<HTMLElement>('[data-steps]');
    if (reducedMotion) {
      setActiveStep(3);
    } else if (steps) {
      let currentStep = 0;
      let intervalId: number | null = null;
      const updateStep = () => {
        setActiveStep(currentStep);
        currentStep = (currentStep + 1) % 4;
      };
      const stepObserver = new IntersectionObserver(([entry]) => {
        if (entry?.isIntersecting && intervalId === null) {
          updateStep();
          intervalId = window.setInterval(updateStep, 1400);
        } else if (!entry?.isIntersecting && intervalId !== null) {
          window.clearInterval(intervalId);
          intervalId = null;
          setActiveStep(-1);
        }
      }, { threshold: 0.3 });
      stepObserver.observe(steps);

      return () => {
        revealObserver.disconnect();
        stepObserver.disconnect();
        if (intervalId !== null) window.clearInterval(intervalId);
        if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
      };
    }

    return () => {
      revealObserver.disconnect();
      if (animationFrame.current !== null) window.cancelAnimationFrame(animationFrame.current);
    };
  }, []);

  useEffect(() => {
    pageRef.current?.querySelector<HTMLElement>('[data-steps]')?.style.setProperty(
      '--step-progress',
      `${Math.max(0, activeStep) / 3 * 100}%`,
    );
  }, [activeStep]);

  const visibleScores = scores.slice(0, shownStudents);
  const atRisk = visibleScores.filter((score) => score >= 70).length;
  const needsAttention = visibleScores.filter((score) => score >= 40 && score < 70).length;
  const onTrack = visibleScores.filter((score) => score < 40).length;

  const scrollToHow = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    if (window.location.hash !== '#how') window.history.pushState(null, '', '#how');
    document.getElementById('how')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    });
  };

  return (
    <MarketingShell showProgress>
      <div ref={pageRef}>
        <nav className={styles.nav} aria-label="Main navigation">
          <a className={styles.logo} href="#top"><i aria-hidden="true">A</i>Academic AI for Impact</a>
          <Link className={styles.button} href="/login">Log in</Link>
        </nav>
        <main id="top" className={styles.main}>
          <div className={styles.hero}>
            <div className={styles.heroCopy}>
              <h1 className={styles.heroTitle}>
                <span><b>Spot who needs help,</b></span>
                <span><b>weeks earlier,</b></span>
                <span><b>and act in time.</b></span>
              </h1>
              <p className={styles.lead}>Academic AI for Impact watches attendance, internal marks and submissions to flag at-risk students early, and shows you exactly why. Support starts sooner, and you stay in control of every decision.</p>
              <div className={styles.cta}>
                <Link className={`${styles.button} ${styles.primaryButton}`} href="/login">Log in to your workspace</Link>
                <a className={styles.button} href="#how" onClick={scrollToHow}>See how it works</a>
              </div>
            </div>
            <div className={styles.stage}>
              <article className={styles.warningWindow} onPointerMove={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                const x = (event.clientX - bounds.left) / bounds.width - 0.5;
                const y = (event.clientY - bounds.top) / bounds.height - 0.5;
                event.currentTarget.style.transform = `rotateY(${x * 16}deg) rotateX(${-y * 16}deg)`;
              }} onPointerLeave={(event) => { event.currentTarget.style.transform = ''; }}>
                <div className={styles.windowBar}>
                  <i aria-hidden="true" /><i aria-hidden="true" /><i aria-hidden="true" />
                  <span>Early warnings · Class D <small>Sample data</small></span>
                  <em className={styles.live}>Demo</em>
                </div>
                <div className={styles.windowPane}>
                  <div className={styles.summary} aria-label="Sample data summary">
                    <div><small>At risk</small><strong>{atRisk}</strong></div>
                    <div><small>Needs attention</small><strong>{needsAttention}</strong></div>
                    <div><small>On track</small><strong>{onTrack}</strong></div>
                  </div>
                  <div className={styles.studentList} aria-label="Sample student risk rows">
                    {DEMO_STUDENTS.map((student, index) => {
                      const score = scores[index] ?? 0;
                      const status = score >= 70 ? 'At risk' : score >= 40 ? 'Needs attention' : 'On track';
                      const tone = score >= 70 ? styles.red : score >= 40 ? styles.amber : styles.mint;
                      const selected = reviewedStudent === index;
                      return (
                        <button
                          type="button"
                          className={`${styles.studentRow} ${index < shownStudents ? styles.rowVisible : ''} ${selected ? styles.reviewed : ''} ${signalStudent === index ? styles.signalPulse : ''}`}
                          key={student.name}
                          aria-pressed={selected}
                          onClick={() => setReviewedStudent(selected ? null : index)}
                        >
                          <span className={styles.studentCopy}><b>{student.name}</b><small>{student.reason}{selected ? ' · Reviewed ✓' : ''}</small></span>
                          <span className={styles.rowTrack} aria-label={`Illustrative risk ${score}%`}><i style={{ width: `${score}%` }} className={tone} /></span>
                          <span className={`${styles.chip} ${tone}`}>{status}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className={`${styles.alert} ${alertVisible ? styles.alertVisible : ''}`} aria-live="polite">
                    <b>New signal</b>Demo Student 015: attendance dropped 18% this week
                  </div>
                </div>
              </article>
            </div>
          </div>

          <div className={styles.marquee} aria-label="Example academic subjects">
            <div className={styles.marqueeTrack}>
              {[0, 1, 2, 3].map((copy) => (
                <div key={copy} aria-hidden={copy !== 0}>
                  {DEMO_SUBJECTS.map((subject) => <span key={subject}>{subject}</span>)}
                </div>
              ))}
            </div>
          </div>

          <section id="how" className={styles.section}>
            <h2 className={`${styles.reveal} ${styles.sectionTitle}`}>From early signal to timely support</h2>
            <p className={`${styles.reveal} ${styles.sectionLead}`}>Every alert shows its reasons, and nothing reaches a student until you approve it.</p>
            <div className={styles.steps} data-steps>
              {[
                ['Collect signals', 'Attendance, internal marks and submissions flow in.'],
                ['Score the risk', 'AI ranks students and explains what changed.'],
                ['Alert you early', 'See who needs attention before the next assessment.'],
                ['Support them', 'Plan recovery with topics and quizzes you approve.'],
              ].map(([title, description], index) => (
                <article className={`${styles.step} ${activeStep === index ? styles.stepActive : ''}`} key={title}>
                  <b>{index + 1}</b><h3>{title}</h3><p>{description}</p>
                </article>
              ))}
            </div>
          </section>

          <section className={styles.section}>
            <h2 className={`${styles.reveal} ${styles.sectionTitle}`}>Built around how teachers work</h2>
            <div className={styles.cardGrid}>
              <article
                className={`${styles.card} ${styles.riskCard} ${styles.reveal}`}
                onPointerMove={(event) => {
                  const bounds = event.currentTarget.getBoundingClientRect();
                  event.currentTarget.style.setProperty('--x', `${event.clientX - bounds.left}px`);
                  event.currentTarget.style.setProperty('--y', `${event.clientY - bounds.top}px`);
                }}
              >
                <h3>Early warnings</h3>
                <p>See which classes are slipping while there is still time to act.</p>
                <small className={styles.illustrative}>Illustrative class risk percentages · sample data</small>
                <div className={styles.riskBars}>
                  {DEMO_CLASS_RISKS.map((risk) => (
                    <div key={risk.name}>
                      <span>{risk.name}</span>
                      <i><s data-risk-fill={risk.value} style={{ background: risk.color }} /></i>
                      <span>{risk.value}%</span>
                    </div>
                  ))}
                </div>
              </article>
              <article className={`${styles.card} ${styles.gainCard} ${styles.reveal}`} data-gain-card>
                <h3>Mastery growth</h3>
                <p>Average mark gain after an AI-guided practice cycle in the demo data.</p>
                <div className={styles.bigNumber}>+{gain}</div>
                <p>Illustrative marks out of 25; not a measured result.</p>
              </article>
            </div>
          </section>

          <section className={styles.final}>
            <h2 className={`${styles.reveal} ${styles.gradientTitle}`}>Your students are waiting on the next step.</h2>
            <Link className={`${styles.button} ${styles.primaryButton} ${styles.reveal}`} href="/login">Log in to Academic AI</Link>
          </section>
        </main>
        <footer className={styles.footer}>© 2026 Academic AI for Impact</footer>
      </div>
    </MarketingShell>
  );
}

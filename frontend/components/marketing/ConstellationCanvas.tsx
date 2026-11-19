'use client';

import { useEffect, useRef } from 'react';
import styles from './marketing.module.css';

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
};

export default function ConstellationCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;

    let width = 0;
    let height = 0;
    let frame = 0;
    let isVisible = !document.hidden;
    let pointerX = -999;
    let pointerY = -999;
    let particles: Particle[] = [];
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const color = getComputedStyle(canvas).getPropertyValue('--dot').trim() || '241,242,248';

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      const count = Math.min(90, Math.max(12, Math.floor(width * height / 16000)));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35,
      }));
    };

    const draw = () => {
      context.clearRect(0, 0, width, height);
      for (let index = 0; index < particles.length; index += 1) {
        const particle = particles[index];
        if (!particle) continue;
        if (!reducedMotion) {
          particle.x += particle.vx;
          particle.y += particle.vy;
          if (particle.x < 0 || particle.x > width) particle.vx *= -1;
          if (particle.y < 0 || particle.y > height) particle.vy *= -1;
        }

        const pointerDistance = Math.hypot(particle.x - pointerX, particle.y - pointerY);
        if (!reducedMotion && pointerDistance < 140 && pointerDistance > 0) {
          particle.x += ((particle.x - pointerX) / pointerDistance) * 1.2;
          particle.y += ((particle.y - pointerY) / pointerDistance) * 1.2;
        }

        context.fillStyle = `rgba(${color},.55)`;
        context.beginPath();
        context.arc(particle.x, particle.y, 1.6, 0, Math.PI * 2);
        context.fill();

        for (let nextIndex = index + 1; nextIndex < particles.length; nextIndex += 1) {
          const next = particles[nextIndex];
          if (!next) continue;
          const distance = Math.hypot(particle.x - next.x, particle.y - next.y);
          if (distance < 120) {
            context.strokeStyle = `rgba(${color},${0.16 * (1 - distance / 120)})`;
            context.beginPath();
            context.moveTo(particle.x, particle.y);
            context.lineTo(next.x, next.y);
            context.stroke();
          }
        }

        if (pointerDistance < 170) {
          context.strokeStyle = `rgba(255,59,92,${0.5 * (1 - pointerDistance / 170)})`;
          context.beginPath();
          context.moveTo(particle.x, particle.y);
          context.lineTo(pointerX, pointerY);
          context.stroke();
        }
      }

      if (!reducedMotion && isVisible) frame = window.requestAnimationFrame(draw);
    };

    const onPointerMove = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
    };
    const onResize = () => {
      resize();
      window.cancelAnimationFrame(frame);
      if (!reducedMotion && isVisible) frame = window.requestAnimationFrame(draw);
      else draw();
    };
    const onVisibilityChange = () => {
      isVisible = !document.hidden;
      if (isVisible && !reducedMotion) {
        window.cancelAnimationFrame(frame);
        frame = window.requestAnimationFrame(draw);
      } else {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
    };

    resize();
    if (!reducedMotion && isVisible) frame = window.requestAnimationFrame(draw);
    else draw();
    window.addEventListener('resize', onResize);
    window.addEventListener('pointermove', onPointerMove, { passive: true });
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);

  return <canvas ref={canvasRef} className={styles.canvas} aria-hidden="true" />;
}

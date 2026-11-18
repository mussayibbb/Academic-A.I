# Performance Optimization Report

**Measured:** 2026-11-17  
**Project:** `frontend` (Next.js 15.5.26, App Router)  
**Outcome:** No application-source optimizations applied. The available opportunities could change rendering or timing, which the acceptance criteria prohibit without approval.

## Executive summary

- Production builds pass. The largest First Load JS is `/teacher/dashboard` at **289 kB**, followed by `/student/dashboard` at **219 kB**, `/teacher/analytics` at **211 kB**, and `/teacher/risk-monitoring` at **211 kB**.
- The production route manifest and emitted client chunks show a **Three.js/WebGL** cost on the teacher dashboard and a shared **Recharts** chunk on three chart routes.
- No safe optimization with a demonstrated benefit and zero visual/timing effect was identified. Code splitting either dependency would defer visible content.
- A Lighthouse performance audit could not run: Chrome DevTools MCP did not initialize (lifecycle negotiation timed out after 60 seconds), and no Lighthouse CLI/package is installed. No Lighthouse, LCP, INP, CLS, or TBT values are claimed.
- Since no application code changed, the verification build has the same route sizes as the baseline.

## Measurement method and worktree safety

The initial Git status contained **60 modified files, 35,028 deleted files, and 63 untracked files**. These changes predated this task and were not reverted, staged, or overwritten. The deleted generated output includes the frontend's `.next` tree. To avoid recreating it in the worktree, both baseline and verification builds ran from isolated temporary copies of the frontend source, with `node_modules` linked for dependency resolution. Environment files and existing `.next` output were excluded.

The baseline `npm run build` completed successfully (Next.js reported 48 seconds to compile). The final verification build also completed successfully (10.6 seconds to compile; warm-cache build time is not treated as an optimization). Route sizes below are Next.js's reported production-build values. The unmodified source was built again for final verification; therefore before/after values match.

## Baseline route sizes and First Load JS

`Size` and `First Load JS` are the values reported by `next build`, in kB (or bytes where shown). `First Load JS` includes the shared **103 kB** baseline chunk total. No source optimization was applied, so the final-build values are identical.

| Route | Route size | First Load before | First Load after |
|---|---:|---:|---:|
| `/` | 133 B | 103 kB | 103 kB |
| `/_not-found` | 1 kB | 104 kB | 104 kB |
| `/icon.svg` | 0 B | 0 kB | 0 kB |
| `/login` | 3.94 kB | 142 kB | 142 kB |
| `/student/assignments` | 4.46 kB | 111 kB | 111 kB |
| `/student/dashboard` | 7.6 kB | 219 kB | 219 kB |
| `/student/profile` | 2.71 kB | 109 kB | 109 kB |
| `/student/recovery` | 7.74 kB | 159 kB | 159 kB |
| `/student/subjects` | 2.52 kB | 113 kB | 113 kB |
| `/student/subjects/[subjectId]` | 4.13 kB | 151 kB | 151 kB |
| `/student/support` | 3.31 kB | 110 kB | 110 kB |
| `/teacher/academic-data` | 4.7 kB | 111 kB | 111 kB |
| `/teacher/analytics` | 4.16 kB | 211 kB | 211 kB |
| `/teacher/assignments` | 27.9 kB | 135 kB | 135 kB |
| `/teacher/assignments/[assignmentId]/submissions` | 5.84 kB | 113 kB | 113 kB |
| `/teacher/dashboard` | 147 kB | 289 kB | 289 kB |
| `/teacher/early-warnings` | 3.79 kB | 111 kB | 111 kB |
| `/teacher/internal-marks` | 6.21 kB | 113 kB | 113 kB |
| `/teacher/interventions` | 4.55 kB | 111 kB | 111 kB |
| `/teacher/recovery` | 5.56 kB | 148 kB | 148 kB |
| `/teacher/recovery/roadmaps` | 13.6 kB | 167 kB | 167 kB |
| `/teacher/recovery/students/[studentId]` | 9.12 kB | 116 kB | 116 kB |
| `/teacher/recovery/subjects/[subjectId]` | 3.17 kB | 121 kB | 121 kB |
| `/teacher/recovery/subjects/[subjectId]/students/[studentId]` | 2.94 kB | 121 kB | 121 kB |
| `/teacher/reports` | 3.44 kB | 110 kB | 110 kB |
| `/teacher/risk-monitoring` | 4.29 kB | 211 kB | 211 kB |
| `/teacher/settings` | 3.03 kB | 110 kB | 110 kB |
| `/teacher/students` | 9.81 kB | 152 kB | 152 kB |
| `/teacher/students/[studentId]` | 8.56 kB | 115 kB | 115 kB |
| `/teacher/subject-modules` | 133 B | 103 kB | 103 kB |

## Lighthouse and runtime metrics

| Metric for `/teacher/dashboard` | Baseline | After |
|---|---|---|
| Lighthouse performance score | Not measured | Not measured |
| First Contentful Paint | Not measured | Not measured |
| Largest Contentful Paint | Not measured | Not measured |
| Total Blocking Time | Not measured | Not measured |
| Cumulative Layout Shift | Not measured | Not measured |
| Interaction to Next Paint | Not measured | Not measured |

The browser measurement attempt failed because the Chrome DevTools MCP lifecycle negotiation did not complete within 60 seconds. The browser tools therefore could not open or audit the local route. The installed frontend dependencies contain neither a Lighthouse package nor a Lighthouse executable, and the available DevTools Lighthouse tool does not report performance metrics. The user opted to skip MCP configuration troubleshooting. No values have been inferred or fabricated.

## Bundle and dependency measurements

No `@next/bundle-analyzer`, `webpack-bundle-analyzer`, source-map-explorer, or Lighthouse package is installed. The installed Next.js CLI also exposes no analyzer command, and the production client output contains no source maps. Consequently, a module-level treemap is unavailable. The following evidence comes from emitted production JS files, gzip measurements, route manifests, and matching module markers; it is not represented as a full bundle-analyzer report.

| Rank | Emitted chunk / evidence | Raw size | Gzip size | Route association |
|---:|---|---:|---:|---|
| 1 | `5611-60704d595af94ba0.js` — contains Recharts markers | 373,633 B (364.9 KiB) | 100,661 B (98.3 KiB) | `/student/dashboard`, `/teacher/analytics`, `/teacher/risk-monitoring` |
| 2 | `a64e2368-8c60b82c8fef8b1b.js` — contains `WebGLRenderer` | 356,820 B (348.5 KiB) | 84,732 B (82.7 KiB) | `/teacher/dashboard` |
| 3 | `7b0450f6-b82d47028865f525.js` — contains `WebGLRenderer` and `CanvasTexture` | 202,961 B (198.2 KiB) | 53,259 B (52.0 KiB) | `/teacher/dashboard` |
| 4 | `6229-8185e5c29d16b298.js` — shared client chunk, module attribution unavailable | 174,214 B (170.1 KiB) | 46,546 B (45.5 KiB) | Shared |
| 5 | `e990887d-9a74290b01ee4208.js` — shared client chunk, module attribution unavailable | 173,024 B (169.0 KiB) | 54,362 B (53.1 KiB) | Shared |
| 6 | `framework-5c50cd8c7546a9e8.js` | 139,850 B (136.6 KiB) | 44,869 B (43.8 KiB) | Shared runtime |
| 7 | `main-082f08cdad6bdf03.js` | 129,378 B (126.3 KiB) | 37,762 B (36.9 KiB) | Shared runtime |
| 8 | `polyfills-42372ed130431b0a.js` | 112,594 B (110.0 KiB) | 39,627 B (38.7 KiB) | Shared runtime |
| 9 | `6150-723bfbd52296e0c1.js` | 108,816 B (106.3 KiB) | 35,285 B (34.5 KiB) | Multiple routes |
| 10 | `4421-ff293ff73c24d6c6.js` | 81,684 B (79.8 KiB) | 22,784 B (22.3 KiB) | Multiple routes |

The two dashboard chunks containing WebGL markers total **559,781 B raw / 137,991 B gzip**. The Recharts chunk totals **373,633 B raw / 100,661 B gzip**. `AcademicSolarSystem.tsx` imports Three.js directly; chart routes import Recharts directly. Exact per-package attribution beyond these marker/manifest matches is unavailable without a module analyzer.

## Ranked bottlenecks

1. **Teacher dashboard's Three.js/WebGL payload and rendering work.** The route has the largest measured First Load JS (**289 kB**, versus 103 kB shared). Its route manifest includes two WebGL-marked chunks totaling about **134.8 KiB gzip**. `frontend/app/teacher/dashboard/page.tsx` imports `AcademicSolarSystem` synchronously, and `frontend/components/AcademicSolarSystem.tsx` imports Three.js, builds canvas textures and shadowed geometry, and starts a continuous renderer loop when reduced motion is off. Bundle evidence is strong; actual main-thread/GPU cost was not traceable without browser tools.
2. **Recharts shared by three data-visualization routes.** One **98.3 KiB gzip** chunk is included in the manifests for Student Dashboard, Teacher Analytics, and Risk Monitoring. Their First Load JS values are **219 kB**, **211 kB**, and **211 kB**, respectively. Imports are visible in `frontend/app/student/dashboard/page.tsx`, `frontend/app/teacher/analytics/page.tsx`, and `frontend/app/teacher/risk-monitoring/page.tsx`.
3. **Shared initial JavaScript.** Next reports **103 kB First Load JS shared across all routes**. The two largest generic shared chunks total about **98.5 KiB gzip**; module-level ownership could not be separated without source maps or a bundle analyzer. `frontend/components/PortalShell.tsx` and `frontend/components/ui.tsx` are client-side shared UI and import Framer Motion and Lucide icons, but no precise size is attributed to either package here.

## Optimizations applied

**None.** All likely reductions found would defer or remove visible behavior: loading Three.js on demand could change hero/orb timing or introduce a fallback/layout shift; loading Recharts on demand could delay charts or change layout timing; changing shared motion/icon usage could affect animations or interactions. No safe, measurable optimization with guaranteed identical rendering was identified. No source file was modified, added, or deleted.

## Needs my approval

| Candidate | Expected benefit | Risk / why approval is needed |
|---|---|---|
| Load the Three.js hero on demand | Could reduce the dashboard's initial transfer by up to the measured ~134.8 KiB gzip associated with the two WebGL chunks, depending on how the split is implemented | Defers the orb and may change its first render, loading state, perceived layout stability, or interaction timing |
| Load Recharts only when a chart is needed | Could avoid the measured ~98.3 KiB gzip chart chunk on initial loads that do not need a chart | Current charts render in the page; deferral changes content timing and may require a placeholder or affect layout |
| Replace or defer shared animation/icon code | Potentially lower shared JS if a module analyzer confirms the opportunity | No reliable package-level savings are measured; replacing shared UI can alter animation, appearance, or interactions |

## Issues intentionally left untouched

- The Three.js scene continuously animates when reduced motion is not requested and enables antialiasing and shadow mapping. This is a potential CPU/GPU cost observed in source, not a Lighthouse/trace-confirmed bottleneck. Reducing or stopping it would change animation behavior.
- The large generated `.next` and `.venv` worktree deletions and all other pre-existing changes were left untouched. Builds ran only in temporary copies.
- No package versions or dependencies were changed, and no commits were created because the worktree was already dirty.

## Files changed by this task

| Change | File |
|---|---|
| Added | `PERFORMANCE_OPTIMIZATION_REPORT.md` |
| Modified | None |
| Deleted | None |

Pre-existing worktree changes are not included in this task's file-change list and were preserved.

## Verification

All commands ran against an isolated copy of the current frontend source to avoid changing the existing worktree:

| Check | Result |
|---|---|
| Baseline production build | Pass |
| Final `npm run typecheck` | Pass |
| Final `npm run lint` | Pass |
| Final `npm run build` | Pass; all 26 static-generation steps completed |
| Lighthouse / browser performance trace | Unavailable for the reasons documented above |

## Manual regression-testing checklist

- [ ] Compare `/teacher/dashboard` at desktop and mobile widths; verify the hero, 3D orb, stat cards, and all dashboard panels remain visually unchanged.
- [ ] Verify the Three.js scene still initializes, animates, resizes, and falls back when WebGL is unavailable.
- [ ] Check the reduced-motion preference and confirm the scene and existing page animations retain their intended reduced-motion behavior.
- [ ] Verify Student Dashboard, Teacher Analytics, and Risk Monitoring charts render with representative API data and with empty/error responses.
- [ ] Exercise remaining teacher/student navigation links, workspace search, keyboard navigation, collapsed sidebar, and mobile menu.
- [ ] Repeat an authenticated browser Lighthouse run for `/teacher/dashboard` once Chrome DevTools MCP or Lighthouse CLI is available; compare runs under the same device profile and network/CPU settings.

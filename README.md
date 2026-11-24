# Academic AI for Impact

An early-warning and academic support demo built on the existing repository. It provides separate faculty and student workspaces, protected student records, assignment uploads and grading, dated momentum signals, a trainable risk model, SHAP explanations, institutional-source retrieval, and reviewed intervention drafts.

All current sample records are fictional **DEMO DATA**. The demo classifier is trained and evaluated against generated labels; its reported scores are not evidence of performance on a real institution. Do not connect this build to live student records without institutional privacy, governance, security, accessibility, and model-validation review.

## Requirements

- Python 3.10 or newer
- Node.js 20 or newer and npm
- SQLite for local work; PostgreSQL is supported for deployment
- Optional for semantic document embeddings: install `requirements-ai.txt` and download the configured Sentence Transformers model
- Optional for XGBoost comparison: install `requirements-ai.txt`
- Optional local LLM: Ollama installed separately with a local model pulled
- Optional research interface: install `requirements-demo.txt`

## Local Setup (PowerShell)

From the repository root:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Replace `JWT_SECRET` in `.env` with a unique, random secret before deployment. Then:

```powershell
Push-Location frontend
npm ci
Pop-Location
```

## Database and Demo Data

Schema changes are explicit Alembic migrations. Application startup does not create tables or seed users.

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m backend.app.services.seed_demo
```

The seed command is deterministic and creates 248 fictional students and dated observations, assignments, submissions, snapshots, and draft interventions. Seed labels are historical example data; current HIGH/MEDIUM/LOW distribution is calculated from canonical model predictions and can differ. The checked-in database currently predicts 52 HIGH, 83 MEDIUM, and 113 LOW.

Demo sign-ins:

- Faculty: `teacher@example.com` / `teacher123`
- Student: `student@example.com` / `student123`

These credentials are only for local demo data. Do not reuse them in a deployed environment.

## Run the Services

Terminal 1, repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
Push-Location frontend
npm run dev
Pop-Location
```

Open <http://localhost:3000>. The API health endpoint is <http://localhost:8000/health>; authenticated API documentation is available at <http://localhost:8000/docs>.

## Academic Data Imports

Apply schema updates with `alembic upgrade head`, then open **Academic Data** in the faculty workspace. CSV and XLSX imports support marks, assessments, attendance, and learning activities; the page displays the required columns for each record type. Student matching uses existing student IDs, import batches retain source/checksum/row status metadata, and repeated files or rows are not applied twice. Imported observations feed current risk features without replacing student profile values or assignment submission uploads.

The **Internal Marks** register stores subject-scoped EVS, CN, TOC, RM, and SE records by academic period (`YYYY-S1` / `YYYY-S2`). Import `subject_records` from CSV/XLSX with `USN`, `Subject`, `Academic_Period`, and any of `FA1`, `FA2`, `Assignment`, and `Attendance`; per-field `*_Observed_At` dates are retained for temporal alignment. Corrections update only the same student/subject/period and append revision history. Independent risk outcomes can be imported as `risk_outcomes` only with a documented source; model predictions and labels derived from the same marks are rejected as targets. Internal marks use `((FA1 + FA2) / 2) + Assignment`; subject marks remain outside the existing global risk model.

The future-only observation export and readiness report are available at `GET /api/v1/ml/academic-data/export.csv` and `GET /api/v1/ml/academic-data/readiness`, and the CLI exporter is `python -m ai.risk.academic_dataset`. They never train a model. Readiness requires complete, time-aligned marks plus independent outcomes, three periods, at least 100 aligned observations, 20 distinct students in each risk class, clean values/identities, and an approved global aggregation strategy. Current data is not ready.

## Student Management

The faculty **Students** directory supports filtered and paginated browsing, record creation/editing/deletion, and CSV import/export. New manual records are labeled `MANUALLY ADDED`; CSV imports must be explicitly classified as `DEMO DATA` or `INSTITUTIONAL IMPORT`. Institutional imports require a source description and a verification attestation, and re-importing the same file is idempotent. Existing records without reliable provenance remain `LEGACY / UNVERIFIED`; editing a record cannot change its origin. When the active model is demo-trained, only sufficiently supported `DEMO DATA` records are scored. Other records show an unavailable risk status rather than a synthetic-model score.

Phase 3 adds teacher-only real-data validation, coverage, deterministic dataset fingerprints, safe candidate CSV export, temporal candidate-training utilities, candidate evaluation/SHAP contracts, and blocked promotion/rollback controls. See [docs/phase3.md](docs/phase3.md). Candidate training is separate from the production model; current measured data does not pass readiness, so no real-data candidate is trained.

## Subject Recovery

The teacher Recovery workspace uses existing classes, subjects, students, and FA-1 marks. Apply the Alembic migrations with `alembic upgrade head`; teachers can then upload text-searchable PDFs to the shared subject module library, review extracted topics and generated quizzes, and assign dated practice. Students see due quizzes on **Student → Recovery**. Assignment notices use the existing in-app notification system.

The defaults are a pass mark of 12 on a 25-mark scale. Configure `RECOVERY_PASS_MARK`, `RECOVERY_MAXIMUM_MARKS`, and `RECOVERY_AI_RATE_LIMIT_PER_HOUR` in `.env` to change the thresholds or per-teacher hourly AI request limit. AI calls run only on the backend, using the configured local Ollama provider or Gemini credentials; keep `GEMINI_API_KEY` private. PDFs without extractable text (including image-only scans) are rejected; OCR is not provided. Quiz validation requires generated answer evidence to occur in the selected module text. Short-answer grading is deterministic and requires a normalized exact match, while multiple-choice answers are graded by option key.

Generation jobs expose queued/running/completed/failed status, poll persisted state, retry failures up to three times, and allow teachers to retry a job that remains stalled beyond the configured provider timeout. They run in FastAPI background tasks rather than a separate durable queue. Focused workflow checks are:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_recovery_quizzes.py
Push-Location frontend
npm test -- app/teacher/recovery/page.test.tsx components/StudentRecoveryQuizzes.test.tsx
npm run typecheck
Pop-Location
```

## Research and Demo Interface

The optional Streamlit app calls the existing FastAPI endpoints for faculty authentication, risk distribution, student records, canonical prediction, SHAP explanation, momentum, and intervention drafts. Install its optional dependencies and run from the repository root:

```powershell
python -m pip install -r requirements-demo.txt
streamlit run streamlit_app.py
```

Set `ACADEMIC_AI_API_URL` if FastAPI is not at `http://127.0.0.1:8000`. When a current HIGH-risk prediction exists, the app selects the existing student with the highest P(HIGH); otherwise it selects the existing record with the highest P(HIGH) and preserves its actual predicted class. Intervention drafts still require faculty review and approval in the faculty workspace.

## Early Warnings

The faculty `/teacher/early-warnings` page uses `GET /api/v1/early-warnings`, `GET /api/v1/early-warnings/{studentId}`, and the what-if/intervention endpoints backed by the existing production model and SQLAlchemy database. The list is paginated in groups of 50 and restricted to each teacher's assigned courses or sections. Priority thresholds are `High >= 0.75`, `Medium >= 0.40`, and `Low < 0.40`; fewer than three usable signals is rendered as insufficient data.

Apply all database migrations before deploying the frontend or starting the weekly job:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Seed only a local demo environment (never production):

```powershell
.\.venv\Scripts\python.exe -m backend.app.services.seed_demo
```

After installing a non-demo model and loading real student data, run the weekly scorer once to verify deployment:

```powershell
.\.venv\Scripts\python.exe -m backend.app.services.score_early_warnings
```

The scorer reads each student's current point-in-time features, stores the model version and `P(HIGH)` as a weekly snapshot, and updates the current week's snapshot on repeat runs. It refuses to run with a model artifact marked `DEMO DATA`. Schedule it on the backend host, not Vercel. Example Linux cron entry for Mondays at 03:00 UTC:

```cron
0 3 * * 1 cd /app && /app/.venv/bin/python -m backend.app.services.score_early_warnings
```

Deploy the Next.js app to Vercel with `NEXT_PUBLIC_API_URL` set to the public FastAPI base URL. On the backend host configure `DATABASE_URL`, a unique `JWT_SECRET`, `CORS_ORIGINS` containing the Vercel origin, and `INSTITUTIONAL_TIMEZONE`; also configure any selected LLM provider credentials and ensure the active model artifact is available to the backend. Run `alembic upgrade head` as a release step before enabling the scheduler. Do not copy the demo seed database into production, and never commit `.env` values or provider secrets.

## Model Pipeline

The preprocessor derives explicit features from database students and dated academic observations, excluding student IDs and risk-probability proxies from model inputs:

```powershell
.\.venv\Scripts\python.exe -m ai.risk.preprocess
.\.venv\Scripts\python.exe -m ai.risk.train
```

The CSV-only demo pipeline uses `data/processed/DSstudents_processed.csv`. It is an explicitly marked synthetic DEMO / TEST dataset and is not an institutional outcome source.

The active model contract excludes FA1, FA2, internal/examination marks, GPA/CGPA, academic assessment scores, and mark-derived trends. It uses only attendance, assignments completed, missing submissions, assignment submission rate, weekly activity, and attendance change. Student IDs and target/risk columns are not model inputs.

Because the CSV has no genuine historical risk outcome, this implementation derives a clearly marked rule-generated three-class target: a composite attendance/submission/LMS risk score, with lower and upper tertiles defining LOW and HIGH and the middle third defining MEDIUM. This is rule-generated preview data, not historical outcome learning.

Training uses a reproducible stratified holdout split and compares Logistic Regression with Random Forest. XGBoost is included only when already installed. The default training command writes a versioned candidate to `models/candidates/risk_model_candidate.joblib` plus a candidate metrics JSON; it does not replace `models/risk/risk_model.joblib`.

The candidate artifact contains the preprocessing pipeline, ordered feature contract, three-class label encoder, metrics, dataset checksum and row count, training timestamp, target-label provenance, and DEMO-data indicator. Replacing the active artifact requires the existing registry, checksum validation and independent teacher approval workflow.

## Explanations and Momentum

SHAP uses the saved classifier, that student's actual model features, and persisted training background. Student 360 exposes the computed local contributions and a model-version tag; the contribution chart is scaled to the largest absolute value and describes changes to `P(risk_level)`, not causes. Its AI Risk Summary sends only the class probability and top verified feature evidence to the configured LLM, accepts only a structured selection of those feature names, and displays their original values and contributions. Risk-summary generation is capped at 15 seconds so a slow provider cannot hold the SHAP request for the general LLM timeout; if an LLM is unavailable, times out, or returns an invalid selection, the SHAP explanation remains available and the summary is explicitly marked unavailable. Momentum uses timestamped attendance, assessment, activity, and submission observations; fewer than four observations are classified as `INSUFFICIENT_DATA`.

## Institutional Retrieval and Local LLM

No institutional policy is included. Add institution-approved `.txt`, `.md`, `.pdf`, or `.docx` material under `knowledge_base/policies/`, `academic_support/`, `mentoring/`, or `resources/`, then run:

```powershell
.\.venv\Scripts\python.exe -m ai.rag.index_cli
```

Retrieval returns cited chunks and explicitly reports no match when no relevant source is present. The base install uses a labeled local hashing-vectorizer fallback. For Sentence Transformers, install `requirements-ai.txt`; the actual embedding provider is reported in retrieval metadata.

Set `LLM_PROVIDER=ollama` and `LLM_MODEL_NAME=ollama:<model-name>` for a separately installed Ollama model. Set `LLM_PROVIDER=gemini` with `GEMINI_MODEL_NAME` and a private `GEMINI_API_KEY` to opt into Gemini. With no usable external provider configured, intervention generation uses a development text template clearly marked as **not an LLM**. Risk is calculated by the classifier, never by the LLM. Generated messages remain drafts until a faculty member edits/reviews and explicitly approves them.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm test
npm run typecheck
npm run lint
npm run build
Pop-Location
```

## Docker

Copy `.env.example` to `.env`, then set a unique `JWT_SECRET` and `POSTGRES_PASSWORD` before running:

```powershell
docker compose up --build
```

Compose explicitly migrates the PostgreSQL schema before starting the API. Seed demo data separately with `docker compose exec backend python -m backend.app.services.seed_demo`. The local LLM is intentionally not included in Docker; run Ollama on the host and configure an address reachable from the backend container when needed.

For the seeded demo model inside Compose, run:

```powershell
docker compose exec backend python -m ai.risk.preprocess
docker compose exec backend python -m ai.risk.train
```

## Current Scope and Limitations

- Demo data and the generated model are fictional; no real institutional dataset or policy corpus was provided.
- XGBoost and Sentence Transformers are optional extras. Without them the risk service compares the two sklearn baselines and RAG uses its explicit hashing fallback.
- PDF and common image/text files can be previewed inline. Other supported assignment files are downloaded; uploaded files are never executed.
- CSV export is implemented. PDF/Excel export, external message delivery, and production deployment hardening are not included.
- The legacy `setup_project.py` is not used by any setup, build, or runtime command. Do not execute it.
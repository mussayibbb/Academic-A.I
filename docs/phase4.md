# Phase 4: Student Support Intelligence

The production risk model remains the authoritative signal. Phase 4 consumes the verified student risk and SHAP evidence to build a support workflow that is educational, reviewable, and auditable.

## What is included

- Student support profile endpoint using the existing production risk prediction and explanation evidence.
- Personalized recommendation output based on the current risk level and top risk drivers.
- Support action creation for teacher-led academic follow-up work.
- Follow-up scheduling for support tracking and student support continuity.
- Student-level access control so a student can only view their own support profile.
- Persistent recovery roadmaps with teacher review, explicit approval/publication, student task progress and submissions, teacher feedback, and immutable task events.
- Subject-first teacher and student recovery workspaces using the latest persisted assessment per subject, with source denominators and assessment history.
- A versioned 12/25 support threshold, strictly applied after proportional normalization. Subject support status is distinct from explicit official pass/fail results and the global risk model.
- Authorized administrator policy updates append immutable versions and audit records. Without a valid, explicitly sourced maximum or a unique latest assessment, status remains insufficient evidence.
- Browser API requests go through same-origin Next.js rewrites to `API_SERVER_URL`; the backend CORS allowlist remains explicit and is not widened for development tunnels.

## Key API surfaces

- GET /api/v1/students/{student_id}/support-profile
- GET /api/v1/students/{student_id}/support-plan
- GET /api/v1/students/{student_id}/support-timeline
- GET /api/v1/students/{student_id}/support-actions
- POST /api/v1/students/{student_id}/support-actions
- POST /api/v1/interventions/{student_id}/follow-up
- POST /api/v1/students/{student_id}/recovery-plans
- POST /api/v1/students/{student_id}/recovery-blueprints
- GET /api/v1/students/{student_id}/recovery-plans
- PATCH /api/v1/recovery-plans/{plan_id}
- PATCH /api/v1/recovery-plans/{plan_id}/blueprint
- POST /api/v1/recovery-plans/{plan_id}/blueprint/revisions
- POST /api/v1/recovery-plans/{plan_id}/approve
- POST /api/v1/recovery-plans/{plan_id}/reject
- POST /api/v1/recovery-plans/{plan_id}/publish
- GET /api/v1/students/{student_id}/recovery-tasks
- GET /api/v1/students/me/recovery-tasks
- PATCH /api/v1/recovery-tasks/{task_id}
- POST /api/v1/recovery-tasks/{task_id}/submit
- PATCH /api/v1/recovery-tasks/{task_id}/assess
- GET /api/v1/students/{student_id}/recovery-timeline
- GET /api/v1/students/me/recovery-timeline
- GET /api/v1/teacher/recovery-monitoring
- GET /api/v1/teacher/recovery-adaptations
- GET /api/v1/teacher/subject-focus
- GET /api/v1/students/me/subject-focus
- GET /api/v1/admin/subject-focus-policy (administrator only)
- PUT /api/v1/admin/subject-focus-policy (administrator only; appends a version)

`LOW` means the latest uniquely dated, denominator-verified FA-1 assessment for that subject normalizes to strictly below 12/25. The authoritative evidence is a `Mark` joined through `Assessment` and `Course` to the subject: `marks.student_id` identifies the student, `marks.assessment_id` identifies the exact assessment, `marks.recorded_at` supplies the observation date, and `assessments.title` must identify FA-1 using the supported first-internal label patterns. `assessments.maximum_marks` is usable only when `assessments.maximum_marks_verified` is true. Supplying a numeric maximum during import does not verify it; imported assessments remain unverified until an authorized source-verification workflow establishes the denominator. When present, `marks.import_batch_id` and `assessments.import_batch_id` resolve to `academic_data_import_batches.source_filename` for source provenance. Without those records, the assessment identity, date, or denominator is unknown and must not be inferred. `StudentSubjectAcademicRecord.fa1` alone is insufficient because that model has no FA-1 maximum or source field. FA-2 and other assessment records never substitute for FA-1.

For example, 11.99/25 is LOW, exactly 12/25 is not LOW, 18/50 normalizes to 9/25 and is LOW, and 24/50 normalizes to 12/25 and is not LOW. An explicitly recorded official `FAIL` remains a separate support signal and is never inferred from the normalized score; it does not, by itself, qualify a subject for a score-based recovery roadmap. Same-time latest assessments, missing dates, invalid maxima, unverified denominators, or legacy policy versions on a different scale remain insufficient evidence for score-based classification. Later authorized threshold changes append immutable policy versions and audit records; the normalized scale remains 25.

Recovery plan generation is teacher-only and limited to students linked to that teacher's course or section. The evidence-review plan stores source-linked academic records and an available persisted risk prediction as provenance, but does not infer topic mastery from aggregate marks. Recovery-blueprint generation discovers every teacher-scoped subject with qualifying evidence for the selected student; a caller-supplied subject list does not define or cap the roadmap scope. Subjects with insufficient or ambiguous evidence remain visibly reported and are not treated as score-eligible. The optional academic-period label does not assign a term to assessment marks because no period relationship exists on the assessment-mark schema. Blueprint generation also requires an official FA-2 date and either teacher-supplied syllabus context with its source label or explicit acknowledgement that no official mapping is available. It uses only teacher-approved Module 3 and Module 4 topics linked to processed, readable module sources. The configured LLM receives those bounded topic facts and may recommend priorities and durations; strict response validation rejects added, missing, or duplicate topic IDs. If generation fails, the draft reports the failure and does not substitute invented recommendations.

Teacher module uploads are text-extracted before the existing topic-map endpoint invokes the configured LLM. Extracted syllabus candidates remain on their source resource until an authorized teacher approves them. Candidate JSON preserves source numbering/order, nested parent-child relationships, review reasons, exact text evidence, and page/slide locations only when the document reader supplied verifiable location markers. Uncertain or weakly grounded candidates remain flagged for review; they are never made eligible for assignments or blueprints merely because the model returned parseable JSON. Scanned PDFs remain unsupported unless they contain an extractable text layer.

Blueprints persist inside versioned recovery-plan drafts. Their global schedule places all approved Module 3 topics in Stage 1, then Module 4 topics in Stage 2 on later dates, and revision/assessment practice for every eligible subject in Stage 3. Teachers must review and save the proposed dates before publication. Draft topic assignments are hidden from student list and learning endpoints. Teachers can confirm or exclude topics, add official syllabus mappings, resolve source-order conflicts, edit dated schedules, and use the existing assessment workflow to generate, inspect, approve, and publish exactly 15 MCQs and 5 short-answer questions per included topic. Approval/publication is blocked until syllabus review, topic decisions, included-topic sources and mappings, conflicts, global stage order, daily workload, and source-validated published assessments satisfy the checks. Only then are included draft learning assignments activated alongside roadmap publication and dated recovery tasks persisted. Published blueprints can be revised as a new draft version; the revision reuses existing topic assignments and assessments, and publication updates scheduling only for assignments without a study session or assessment attempt. Started/completed assignments, recovery tasks, prior plan versions, and assessment attempts are preserved. Ordinary recovery tasks have no schedule, deadline, or resources unless a teacher explicitly provides them. Students can see and update only their own published tasks. Progress, text submissions, and teacher assessment/feedback are recorded in append-only task events; these recovery tasks do not write official assignment grades or submissions. Task completion or progress does not itself establish learning outcomes or topic mastery.

## Safety boundaries

- Existing model training, deployment, and validation flows are not modified.
- Support actions are evidence-grounded, not predictive replacements.
- Recommendations are generated from actual risk and explanation data rather than fabricated information.

### Recovery adaptation foundation

Teacher assessment events on published recovery tasks create one immutable, evidence-linked adaptation record. Each assessment action requires its own idempotency key; replaying a key returns the original result, while a later assessment after a new submission must use a fresh key. A teacher-requested revision is surfaced for teacher review; a second revision request on the same task is labeled repeated difficulty. A teacher decision to complete a task is recorded as insufficient evidence for topic mastery, regardless of the recovery-task score. These records do not create activities, change a roadmap, alter official grades, or update risk classification. Teachers can retrieve records only for students assigned to their courses/sections; the endpoint supports `student_id`, `limit`, and `offset` filters. Any future material roadmap change must remain behind the existing teacher review and publication process.

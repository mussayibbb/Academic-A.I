'use client';

import { AlertCircle, Check, Download, FileText, Layers3, LoaderCircle, Plus, RefreshCw, Save, UploadCloud } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { API_BASE, api, readToken } from '@/lib/api';
import { ErrorNotice } from '@/components/ui';

const SLOT_NUMBERS = [1, 2, 3, 4, 5];
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const ALLOWED_EXTENSIONS = ['pdf', 'docx', 'pptx', 'txt'];

type Subject = { id: number; code: string; name: string };
type Module = { id: number; subject_id: number; module_number: number; title: string; description: string | null; status: string; resource_count?: number };
type ExtractedTopic = {
  candidate_id?: string; candidate_path?: number[]; original_number?: string | null; title?: string; summary?: string;
  learning_objectives?: string[]; concepts?: string[]; source_order?: number; source_excerpt?: string | null;
  source_locator?: { type: 'page' | 'slide'; value: number } | null; order_uncertain?: boolean;
  hierarchy_uncertain?: boolean; requires_review?: boolean; review_reason?: string | null; children?: ExtractedTopic[];
  course_title?: string | null; module_number?: number; module_title?: string;
};
type Resource = { id: number; module_id: number; title: string; original_filename: string; file_type: string; file_size: number; content_hash: string; processing_status: string; error_message: string | null; extracted_text?: string | null; extracted_topics: ExtractedTopic[]; provenance: Record<string, unknown>; created_at?: string };
type ApprovedTopic = { id: number; module_id: number; title: string; summary: string; learning_objectives: string[]; concepts: string[]; approval_status: string; source_provenance: Record<string, unknown> };
type Assignment = {
  id: number; student_id: number; student_name: string; subject_id: number; module_id: number; approved_topic_id: number;
  title: string; topic_title: string; expected_duration_minutes: number; status: string; attempt_count: number;
  assessments: { id: number; version: number; status: string; question_count: number; validation_results: { valid?: boolean; issues?: string[]; limitation?: string | null }; error_message: string | null }[];
  latest_attempt_id: number | null; latest_attempt: { id: number; status: string; percentage: number | null; review_pending: boolean } | null;
};
type Question = { id: number; question_type: 'MCQ' | 'SHORT_ANSWER'; question: string; options: string[]; marks: number; correct_answer: string; explanation: string; learning_objective: string; rubric: Record<string, unknown>; source_evidence: { ref: string; quote: string }[] };
type LearningAssessment = { id: number; version: number; status: string; question_count: number; validation_results: { valid?: boolean; issues?: string[]; limitation?: string | null }; error_message: string | null; source_evidence: { ref: string; source: string; text: string; score: number }[]; questions: Question[] };
type TopicDraft = { title: string; summary: string; learning_objectives: string; concepts: string };
type ReviewCandidate = { topic: ExtractedTopic; depth: number; parentTitle: string | null };
type ReviewAttempt = { id: number; student_name: string; status: string; percentage: number; questions: { id: number; question_type: string; question: string; marks: number; correct_answer: string; rubric: Record<string, unknown>; answer: { response_text: string | null; selected_choice: string | null; marks_awarded: number; feedback: string; review_required: boolean; review_reason: string | null } | null }[] };

function flattenCandidates(topics: ExtractedTopic[], depth = 0, parentTitle: string | null = null): ReviewCandidate[] {
  return topics.flatMap((topic) => [
    { topic, depth, parentTitle },
    ...flattenCandidates(topic.children ?? [], depth + 1, topic.title ?? null),
  ]);
}

function uploadResource(subjectId: number, moduleId: number, file: File, progress: (percent: number) => void): Promise<Resource> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const form = new FormData();
    form.set('file', file);
    request.open('POST', `${API_BASE}/api/v1/subjects/${subjectId}/modules/${moduleId}/resources`);
    const token = readToken();
    if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);
    request.upload.onprogress = (event) => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
    request.onerror = () => reject(new Error('Upload could not reach the server. Check the connection and retry.'));
    request.onload = () => {
      let body: Resource | { detail?: string | { message?: string } };
      try { body = JSON.parse(request.responseText) as Resource | { detail?: string | { message?: string } }; }
      catch { reject(new Error(`The server returned an unreadable response (${request.status}).`)); return; }
      if (request.status < 200 || request.status >= 300) {
        const detail = (body as { detail?: string | { message?: string } }).detail;
        reject(new Error(typeof detail === 'string' ? detail : detail?.message ?? `Upload failed (${request.status}).`));
        return;
      }
      resolve(body as Resource);
    };
    request.send(form);
  });
}

export default function SubjectModuleRecoveryPanel({
  studentId,
  initialSubjectId,
  subjectFocusReason,
}: {
  studentId?: number;
  initialSubjectId?: number;
  subjectFocusReason?: string;
}) {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [subjectId, setSubjectId] = useState('');
  const [modules, setModules] = useState<Module[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [topics, setTopics] = useState<ApprovedTopic[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [assessments, setAssessments] = useState<Record<number, LearningAssessment[]>>({});
  const [attemptReviews, setAttemptReviews] = useState<Record<number, ReviewAttempt>>({});
  const [moduleTitles, setModuleTitles] = useState<Record<number, string>>({});
  const [moduleDescriptions, setModuleDescriptions] = useState<Record<number, string>>({});
  const [topicDrafts, setTopicDrafts] = useState<Record<string, TopicDraft>>({});
  const [durations, setDurations] = useState<Record<number, number>>({});
  const [reviewReasons, setReviewReasons] = useState<Record<number, string>>({});
  const [overrideReasons, setOverrideReasons] = useState<Record<string, string>>({});
  const [overrideMarks, setOverrideMarks] = useState<Record<string, number>>({});
  const [selectedFiles, setSelectedFiles] = useState<Record<number, File[]>>({});
  const [slotErrors, setSlotErrors] = useState<Record<number, string>>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [upload, setUpload] = useState<{ slot: number; filename: string; percent: number; stage: 'uploading' | 'extracting' } | null>(null);

  const selectedSubject = subjects.find((subject) => String(subject.id) === subjectId);
  const moduleByNumber = useMemo(() => new Map(modules.map((item) => [item.module_number, item])), [modules]);
  const subjectAssignments = assignments.filter((item) => item.subject_id === Number(subjectId));
  const hasStudent = studentId !== undefined && Number.isInteger(studentId) && studentId > 0;
  const hasApprovedSource = topics.some((topic) => (
    topic.approval_status === 'APPROVED'
    && resources.some((resource) => Number(topic.source_provenance.resource_id) === resource.id && Boolean(resource.extracted_text?.trim()))
  ));
  const reload = useCallback(async (currentSubjectId = subjectId) => {
    if (!currentSubjectId) return;
    setLoading(true);
    setError('');
    try {
      const [moduleItems, topicItems, assignmentItems] = await Promise.all([
        api<Module[]>(`/api/v1/subjects/${currentSubjectId}/modules`),
        api<ApprovedTopic[]>(`/api/v1/subjects/${currentSubjectId}/topics`),
        !hasStudent
          ? Promise.resolve([])
          : api<Assignment[]>(`/api/v1/students/${studentId}/topic-learning-assignments`),
      ]);
      const resourceGroups = await Promise.all(moduleItems.map((item) => api<Resource[]>(`/api/v1/subjects/${currentSubjectId}/modules/${item.id}/resources`)));
      const assignmentItemsForSubject = assignmentItems.filter((item) => item.subject_id === Number(currentSubjectId));
      const assessmentGroups = await Promise.all(assignmentItemsForSubject.map(async (item) => [
        item.id,
        await api<LearningAssessment[]>(`/api/v1/topic-learning-assignments/${item.id}/assessments`),
      ] as const));
      setModules(moduleItems);
      setTopics(topicItems);
      setResources(resourceGroups.flat());
      setAssignments(assignmentItems);
      setAssessments(Object.fromEntries(assessmentGroups));
      setSelectedFiles({});
      setSlotErrors({});
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally { setLoading(false); }
  }, [hasStudent, studentId, subjectId]);

  useEffect(() => {
    api<Subject[]>('/api/v1/subjects').then((items) => {
      setSubjects(items);
      const selected = initialSubjectId
        ? items.find((item) => item.id === initialSubjectId)
        : items[0];
      setSubjectId(selected ? String(selected.id) : '');
    }).catch((requestError: Error) => setError(requestError.message));
  }, [initialSubjectId]);

  useEffect(() => { if (subjectId) void reload(subjectId); }, [reload, subjectId]);

  const createModule = async (number: number) => {
    const subjectModule = await api<Module>(`/api/v1/subjects/${subjectId}/modules`, {
      method: 'POST', body: JSON.stringify({ module_number: number, title: moduleTitles[number]?.trim() || `Module ${number}`, description: moduleDescriptions[number]?.trim() || null }),
    });
    setModules((current) => [...current, subjectModule].sort((left, right) => left.module_number - right.module_number));
  };

  const saveModule = async (subjectModule: Module) => {
    setBusy(`module-${subjectModule.id}`);
    setError('');
    try {
      const updated = await api<Module>(`/api/v1/subjects/${subjectId}/modules/${subjectModule.id}`, {
        method: 'PUT', body: JSON.stringify({ title: moduleTitles[subjectModule.module_number] ?? subjectModule.title, description: moduleDescriptions[subjectModule.module_number] ?? subjectModule.description }),
      });
      setModules((current) => current.map((item) => item.id === updated.id ? updated : item));
      setNotice(`Module ${updated.module_number} details saved.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const chooseFiles = (number: number, files: File[]) => {
    const invalid = files.find((file) => !ALLOWED_EXTENSIONS.includes(file.name.split('.').pop()?.toLowerCase() ?? ''));
    const oversized = files.find((file) => file.size > MAX_FILE_SIZE);
    if (invalid || oversized) {
      setSlotErrors((current) => ({ ...current, [number]: invalid ? `${invalid.name}: only PDF, DOCX, PPTX, and TXT files are supported.` : `${oversized?.name} exceeds the 10 MiB per-file limit.` }));
      setSelectedFiles((current) => ({ ...current, [number]: [] }));
      return;
    }
    setSlotErrors((current) => ({ ...current, [number]: '' }));
    setSelectedFiles((current) => ({ ...current, [number]: files }));
  };

  const extractTopicMap = async (resource: Resource, number: number, filename = resource.original_filename) => {
    setUpload({ slot: number, filename, percent: 100, stage: 'extracting' });
    const result = await api<Partial<Resource>>(
      `/api/v1/subjects/${subjectId}/modules/${resource.module_id}/resources/${resource.id}/extract-topics`,
      { method: 'POST' },
    );
    return { ...resource, ...result } as Resource;
  };

  const uploadFiles = async (number: number) => {
    const files = selectedFiles[number] ?? [];
    if (!files.length) return;
    setBusy(`upload-${number}`);
    setError('');
    setSlotErrors((current) => ({ ...current, [number]: '' }));
    try {
      let subjectModule = moduleByNumber.get(number);
      if (!subjectModule) {
        await createModule(number);
        subjectModule = await api<Module[]>(`/api/v1/subjects/${subjectId}/modules`).then((items) => items.find((item) => item.module_number === number));
      }
      if (!subjectModule) throw new Error(`Module ${number} could not be loaded after creation.`);
      for (const file of files) {
        setUpload({ slot: number, filename: file.name, percent: 0, stage: 'uploading' });
        try {
          let resource = await uploadResource(Number(subjectId), subjectModule.id, file, (percent) => setUpload({ slot: number, filename: file.name, percent, stage: 'uploading' }));
          if (resource.processing_status === 'PROCESSED') {
            try { resource = await extractTopicMap(resource, number, file.name); }
            catch (requestError) { setSlotErrors((current) => ({ ...current, [number]: [current[number], `${file.name}: topic extraction failed: ${(requestError as Error).message}`].filter(Boolean).join('\n') })); }
          }
          setResources((current) => [resource, ...current.filter((item) => item.id !== resource.id)]);
        } catch (requestError) {
          setSlotErrors((current) => ({ ...current, [number]: [current[number], `${file.name}: ${(requestError as Error).message}`].filter(Boolean).join('\n') }));
        }
      }
      setSelectedFiles((current) => ({ ...current, [number]: [] }));
      await reload(subjectId);
    } catch (requestError) { setSlotErrors((current) => ({ ...current, [number]: (requestError as Error).message })); }
    finally { setUpload(null); setBusy(''); }
  };

  const draftFor = (resource: Resource, extracted: ExtractedTopic, index: number): TopicDraft => {
    const key = `${resource.id}:${extracted.candidate_id ?? index}`;
    return topicDrafts[key] ?? { title: extracted.title ?? resource.title, summary: extracted.summary ?? '', learning_objectives: (extracted.learning_objectives ?? []).join('\n'), concepts: (extracted.concepts ?? []).join(', ') };
  };

  const approveTopic = async (resource: Resource, extracted: ExtractedTopic, index: number) => {
    const key = `${resource.id}:${extracted.candidate_id ?? index}`;
    const draft = draftFor(resource, extracted, index);
    if (!draft.title.trim() || draft.summary.trim().length < 10) {
      setSlotErrors((current) => ({ ...current, [modules.find((item) => item.id === resource.module_id)?.module_number ?? 0]: 'Provide a topic title and teacher-reviewed summary of at least 10 characters.' }));
      return;
    }
    setBusy(`approve-${key}`);
    try {
      await api(`/api/v1/subjects/${subjectId}/topics/approve`, { method: 'POST', body: JSON.stringify({
        resource_id: resource.id, candidate_id: extracted.candidate_id,
        source_order: extracted.source_order, order_uncertain: extracted.order_uncertain ?? false,
        title: draft.title.trim(), summary: draft.summary.trim(),
        learning_objectives: draft.learning_objectives.split('\n').map((value) => value.trim()).filter(Boolean),
        concepts: draft.concepts.split(',').map((value) => value.trim()).filter(Boolean), approval_status: 'APPROVED',
      }) });
      await reload(subjectId);
      setNotice(`Approved “${draft.title.trim()}” from ${resource.original_filename}.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const assignTopic = async (topic: ApprovedTopic) => {
    if (!hasStudent) return;
    const duration = durations[topic.id] ?? 30;
    setBusy(`assign-${topic.id}`);
    setError('');
    try {
      await api(`/api/v1/students/${studentId}/topic-learning-assignments`, { method: 'POST', body: JSON.stringify({ approved_topic_id: topic.id, expected_duration_minutes: duration }) });
      await reload(subjectId);
      setNotice(`“${topic.title}” assigned to the selected student for ${duration} minutes.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const generateAssessment = async (assignment: Assignment) => {
    setBusy(`generate-${assignment.id}`);
    setError('');
    try {
      const generated = await api<LearningAssessment>(`/api/v1/topic-learning-assignments/${assignment.id}/generate-assessment`, { method: 'POST' });
      setAssessments((current) => ({ ...current, [assignment.id]: [generated, ...(current[assignment.id] ?? [])] }));
      setNotice(generated.validation_results.valid ? `Draft assessment v${generated.version} generated for teacher review.` : `Generated assessment v${generated.version} requires review; it cannot be published until validation passes.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const assessmentAction = async (assessment: LearningAssessment, action: 'approve' | 'reject' | 'publish', assignmentId: number) => {
    const reason = reviewReasons[assessment.id]?.trim();
    if (action === 'reject' && (!reason || reason.length < 5)) { setError('Enter a rejection reason of at least five characters.'); return; }
    setBusy(`${action}-${assessment.id}`);
    setError('');
    try {
      await api(`/api/v1/learning-assessments/${assessment.id}/${action}`, { method: 'POST', body: JSON.stringify(action === 'reject' || action === 'approve' ? { reason: reason || undefined } : {}) });
      const latest = await api<LearningAssessment[]>(`/api/v1/topic-learning-assignments/${assignmentId}/assessments`);
      setAssessments((current) => ({ ...current, [assignmentId]: latest }));
      setNotice(`Assessment ${action === 'approve' ? 'approved' : action === 'reject' ? 'rejected' : 'published'}.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const openAttemptReview = async (assignment: Assignment) => {
    if (!assignment.latest_attempt_id) return;
    setBusy(`attempt-${assignment.id}`);
    try {
      const review = await api<ReviewAttempt>(`/api/v1/assessment-attempts/${assignment.latest_attempt_id}/review`);
      setAttemptReviews((current) => ({ ...current, [assignment.id]: review }));
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const reviewAnswer = async (assignment: Assignment, questionId: number, decision: 'accept' | 'override') => {
    const key = `${assignment.id}:${questionId}`;
    const reason = overrideReasons[key]?.trim();
    if (decision === 'override' && (!reason || overrideMarks[key] === undefined)) { setError('A grade and reason are required for an override.'); return; }
    setBusy(`answer-${key}`);
    try {
      await api(`/api/v1/assessment-attempts/${assignment.latest_attempt_id}/answers/${questionId}/review`, {
        method: 'PUT', body: JSON.stringify({ decision, ...(decision === 'override' ? { marks_awarded: overrideMarks[key], reason } : {}) }),
      });
      await openAttemptReview(assignment);
      await reload(subjectId);
      setNotice(decision === 'override' ? 'Grade override recorded with its reason.' : 'Response review recorded.');
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setBusy(''); }
  };

  const download = async (assignment: Assignment, resource: Resource) => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/topic-learning-assignments/${assignment.id}/resources/${resource.id}`, { headers: { Authorization: `Bearer ${readToken() ?? ''}` }, cache: 'no-store' });
      if (!response.ok) throw new Error(`Download failed (${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = resource.original_filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (requestError) { setError((requestError as Error).message); }
  };

  const viewResource = async (resource: Resource) => {
    try {
      const response = await fetch(
        `${API_BASE}/api/v1/subjects/${subjectId}/modules/${resource.module_id}/resources/${resource.id}/file`,
        { headers: { Authorization: `****** ?? ''}` }, cache: 'no-store' },
      );
      if (!response.ok) throw new Error(`File could not be opened (${response.status}).`);
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.target = ['PDF', 'TXT'].includes(resource.file_type) ? '_blank' : '_self';
      if (!['PDF', 'TXT'].includes(resource.file_type)) anchor.download = resource.original_filename;
      anchor.rel = 'noreferrer';
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (requestError) { setError((requestError as Error).message); }
  };

  return <section className="surface overflow-hidden rounded-xl" aria-label="Subject recovery modules">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[.07] p-4 sm:p-5">
      <div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg border border-rose-300/15 bg-rose-300/[.06] text-rose-200"><Layers3 size={17} /></span><div><p className="eyebrow">Subject learning loop</p><h2 className="mt-1 text-sm font-semibold text-white">Modules, assessments and progress</h2></div></div>
      <button type="button" onClick={() => void reload()} disabled={loading || !subjectId} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-300 hover:border-rose-300/25 disabled:opacity-50"><RefreshCw size={13} />Refresh</button>
    </div>
    <div className="space-y-4 p-4 sm:p-5">
      {error && <ErrorNotice message={error} />}
      {notice && <div role="status" aria-live="polite" className="rounded-lg border border-emerald-200/15 bg-emerald-200/[.035] px-3 py-2 text-xs text-emerald-100">{notice}</div>}
      {initialSubjectId ? <div className="max-w-xl">
        <p className="text-xs font-medium text-white">{selectedSubject ? `${selectedSubject.code} · ${selectedSubject.name}` : 'Loading selected subject…'}</p>
        {subjectFocusReason && <p className="mt-1 text-[10px] leading-4 text-amber-100">{subjectFocusReason}</p>}
      </div> : <label className="block max-w-xl text-xs text-slate-400" htmlFor="recovery-subject">Subject recovery track
        <select id="recovery-subject" value={subjectId} onChange={(event) => setSubjectId(event.target.value)} disabled={!subjects.length} className="form-input">
          {!subjects.length && <option value="">No authorized subjects available</option>}
          {subjects.map((subject) => <option key={subject.id} value={subject.id}>{subject.code} · {subject.name}</option>)}
        </select>
      </label>}
      {selectedSubject && <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-500"><span>{!hasStudent ? `${selectedSubject.name} module sources are retained with their provenance.` : `Selected student: #${studentId} · ${selectedSubject.name}. Module uploads are retained with their source history.`}</span><span className={`rounded-full border px-2.5 py-1 ${hasApprovedSource ? 'border-emerald-200/20 text-emerald-100' : 'border-amber-200/20 text-amber-100'}`}>{hasApprovedSource ? 'Approved assessment source available' : 'Approved source material needed'}</span></div>}
      {loading ? <div role="status" className="flex items-center gap-2 py-5 text-xs text-slate-400"><LoaderCircle size={14} className="animate-spin" />Loading authorized subject data…</div> : <div className="grid items-start gap-4 xl:grid-cols-2">
        {SLOT_NUMBERS.map((number) => {
          const subjectModule = moduleByNumber.get(number);
          const moduleResources = subjectModule ? resources.filter((resource) => resource.module_id === subjectModule.id) : [];
          const moduleTopics = subjectModule ? topics.filter((topic) => topic.module_id === subjectModule.id) : [];
          const moduleAssignments = subjectAssignments.filter((assignment) => assignment.module_id === subjectModule?.id);
          return <article key={number} className="min-w-0 rounded-xl border border-white/[.08] bg-black/[.12] p-4">
            <div className="flex items-start gap-3"><span className="grid size-8 shrink-0 place-items-center rounded-lg border border-white/10 text-slate-300"><Layers3 size={15} /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="text-xs font-semibold text-white">Module {number}</h3><span className="rounded border border-white/10 px-2 py-0.5 text-[9px] uppercase text-slate-400">{subjectModule?.status ?? 'Not created'}</span></div>{subjectModule && <p className="mt-1 text-[10px] text-slate-500">{subjectModule.resource_count ?? moduleResources.length} resources · {moduleTopics.length} approved topics</p>}</div></div>
            {!subjectModule ? <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
              <label className="sr-only" htmlFor={`recovery-module-title-${number}`}>Module {number} title</label><input id={`recovery-module-title-${number}`} value={moduleTitles[number] ?? ''} onChange={(event) => setModuleTitles((current) => ({ ...current, [number]: event.target.value }))} maxLength={200} placeholder={`Module ${number} title`} className="form-input mt-0" />
              <button type="button" disabled={busy !== ''} onClick={async () => { setBusy(`create-${number}`); try { await createModule(number); setNotice(`Module ${number} created.`); } catch (requestError) { setSlotErrors((current) => ({ ...current, [number]: (requestError as Error).message })); } finally { setBusy(''); } }} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-white/10 px-3 text-xs text-slate-200 disabled:opacity-50"><Plus size={13} />Create empty module</button>
              <label className="text-[10px] text-slate-500 sm:col-span-2">Description<textarea rows={2} value={moduleDescriptions[number] ?? ''} onChange={(event) => setModuleDescriptions((current) => ({ ...current, [number]: event.target.value }))} maxLength={5000} className="form-input resize-y" /></label>
              <label className="block text-[10px] text-slate-400 sm:col-span-2" htmlFor={`recovery-module-files-${number}`}>Upload source files (creates this module slot if needed)
                <input id={`recovery-module-files-${number}`} type="file" multiple accept=".pdf,.docx,.pptx,.txt" disabled={busy !== ''} onChange={(event) => { const selected = Array.from(event.currentTarget.files ?? []); chooseFiles(number, selected); event.currentTarget.value = ''; }} className="form-input file:mr-2 file:border-0 file:bg-transparent file:text-xs file:text-rose-200" />
              </label>
              <p className="text-[10px] text-slate-600 sm:col-span-2">PDF, DOCX, PPTX, TXT · 10 MiB per file. Uploads remain separate source records with provenance.</p>
              {!!selectedFiles[number]?.length && <div className="flex flex-wrap items-center justify-between gap-2 sm:col-span-2"><span className="min-w-0 break-all text-[10px] text-slate-300">{selectedFiles[number].map((file) => file.name).join(', ')}</span><button type="button" disabled={busy !== ''} onClick={() => void uploadFiles(number)} className="inline-flex min-h-9 items-center gap-2 rounded-md bg-rose-600 px-3 text-xs font-semibold text-white disabled:opacity-50"><UploadCloud size={13} />Upload {selectedFiles[number].length}</button></div>}
              {upload?.slot === number && <div role="status" className="sm:col-span-2"><div className="flex justify-between gap-2 text-[10px] text-slate-400"><span className="truncate">{upload.stage === 'extracting' ? 'Extracting topics from' : 'Uploading'} {upload.filename}</span><span>{upload.percent}%</span></div><progress aria-label={`Upload progress for ${upload.filename}`} value={upload.percent} max={100} className="mt-1 h-1.5 w-full accent-rose-400" /></div>}
            </div> : <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
              <label className="sr-only" htmlFor={`recovery-module-title-${number}`}>Module {number} title</label><input id={`recovery-module-title-${number}`} value={moduleTitles[number] ?? subjectModule.title} onChange={(event) => setModuleTitles((current) => ({ ...current, [number]: event.target.value }))} maxLength={200} className="form-input mt-0" />
              <button type="button" aria-label={`Save module ${number}`} disabled={busy !== ''} onClick={() => void saveModule(subjectModule)} className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50"><Save size={13} />Save</button>
              <label className="text-[10px] text-slate-500 sm:col-span-2">Description<textarea rows={2} value={moduleDescriptions[number] ?? subjectModule.description ?? ''} onChange={(event) => setModuleDescriptions((current) => ({ ...current, [number]: event.target.value }))} maxLength={5000} className="form-input resize-y" /></label>
              <label className="block text-[10px] text-slate-400 sm:col-span-2" htmlFor={`recovery-module-files-${number}`}>Add or replace with newer source files
                <input id={`recovery-module-files-${number}`} type="file" multiple accept=".pdf,.docx,.pptx,.txt" disabled={busy !== ''} onChange={(event) => { const selected = Array.from(event.currentTarget.files ?? []); chooseFiles(number, selected); event.currentTarget.value = ''; }} className="form-input file:mr-2 file:border-0 file:bg-transparent file:text-xs file:text-rose-200" />
              </label>
              <p className="text-[10px] text-slate-600 sm:col-span-2">PDF, DOCX, PPTX, TXT · 10 MiB per file. New uploads are retained as separate source records for provenance.</p>
              {!!selectedFiles[number]?.length && <div className="flex flex-wrap items-center justify-between gap-2 sm:col-span-2"><span className="min-w-0 break-all text-[10px] text-slate-300">{selectedFiles[number].map((file) => file.name).join(', ')}</span><button type="button" disabled={busy !== ''} onClick={() => void uploadFiles(number)} className="inline-flex min-h-9 items-center gap-2 rounded-md bg-rose-600 px-3 text-xs font-semibold text-white disabled:opacity-50"><UploadCloud size={13} />Upload {selectedFiles[number].length}</button></div>}
              {upload?.slot === number && <div role="status" className="sm:col-span-2"><div className="flex justify-between gap-2 text-[10px] text-slate-400"><span className="truncate">Uploading {upload.filename}</span><span>{upload.percent}%</span></div><progress aria-label={`Upload progress for ${upload.filename}`} value={upload.percent} max={100} className="mt-1 h-1.5 w-full accent-rose-400" /></div>}
            </div>}
            {slotErrors[number] && <div role="alert" className="mt-3 whitespace-pre-line text-[11px] text-rose-200">{slotErrors[number]}</div>}
            {subjectModule && <div className="mt-3 space-y-3 border-t border-white/[.07] pt-3">
              {!moduleResources.length && <p className="text-[10px] text-slate-500">No source resources in this module yet.</p>}
              {moduleResources.map((resource) => <div key={resource.id} className="rounded-lg border border-white/[.07] p-3">
                <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><div className="break-all text-[11px] font-medium text-slate-200">{resource.original_filename}</div><div className="mt-1 text-[9px] text-slate-500">{resource.file_type} · {Math.ceil(resource.file_size / 1024)} KB · SHA-256 {resource.content_hash.slice(0, 16)}…</div></div><span className={`rounded border px-2 py-1 text-[9px] ${resource.processing_status === 'PROCESSED' ? 'border-emerald-300/15 text-emerald-200' : 'border-rose-300/15 text-rose-200'}`}>{resource.processing_status}</span></div>
                {resource.error_message && <p className="mt-2 text-[10px] text-rose-200">{resource.error_message}</p>}
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><div className="text-[9px] text-slate-500">Source: {String(resource.provenance.source ?? 'teacher upload')} · {resource.created_at ? new Date(resource.created_at).toLocaleString() : ''} · Topic review: {String(resource.provenance.topic_map_status ?? 'NOT STARTED')}</div><button type="button" onClick={() => void viewResource(resource)} className="inline-flex min-h-8 items-center gap-1.5 rounded border border-white/10 px-2 text-[9px] text-slate-300 hover:border-rose-300/20"><FileText size={11} />{['PDF', 'TXT'].includes(resource.file_type) ? 'View file' : 'Download file'}</button></div>
                {resource.extracted_text && <details className="mt-2"><summary className="cursor-pointer text-[10px] text-slate-400">View extracted source text</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] leading-5 text-slate-500">{resource.extracted_text}</pre></details>}
                {!!resource.extracted_topics?.length && <div className="mt-3 space-y-3"><h4 className="text-[9px] font-semibold uppercase text-slate-500">Extracted syllabus candidates · teacher review required</h4>
                  {Boolean((resource.provenance.topic_map_metadata as Record<string, unknown> | undefined)?.course_title) && <p className="text-[10px] text-slate-400">Course title from source: {String((resource.provenance.topic_map_metadata as Record<string, unknown>).course_title)}</p>}
                  {flattenCandidates(resource.extracted_topics).map(({ topic: extracted, depth, parentTitle }, index) => {
                  const key = `${resource.id}:${extracted.candidate_id ?? index}`;
                  const draft = draftFor(resource, extracted, index);
                  const approvedTopic = moduleTopics.find((topic) => Number(topic.source_provenance.resource_id) === resource.id && (
                    extracted.candidate_id
                      ? topic.source_provenance.candidate_id === extracted.candidate_id
                      : topic.title === draft.title
                  ));
                  const approved = approvedTopic?.approval_status === 'APPROVED';
                  const assignment = subjectAssignments.find((item) => item.approved_topic_id === approvedTopic?.id);
                  const locator = extracted.source_locator ? `${extracted.source_locator.type} ${extracted.source_locator.value}` : null;
                  return <div key={key} style={{ marginLeft: `${Math.min(depth, 4) * 12}px` }} className="rounded-md border border-white/[.07] bg-black/10 p-3">
                    <div className="flex items-start justify-between gap-2"><span className="text-[10px] font-medium text-slate-200">{extracted.source_order ?? index + 1}. {extracted.original_number ? `${extracted.original_number} ` : ''}{extracted.title ?? 'Extracted content'}</span>{locator && <span className="text-[9px] text-slate-500">{locator}</span>}</div>
                    {parentTitle && <p className="mt-1 text-[9px] text-slate-500">Subtopic of {parentTitle}</p>}
                    {(extracted.requires_review || extracted.order_uncertain || extracted.hierarchy_uncertain) && <p role="status" className="mt-2 text-[10px] text-amber-200">Teacher review needed: {extracted.review_reason || (extracted.hierarchy_uncertain ? 'Confirm this parent-child relationship against the source.' : extracted.order_uncertain ? 'Confirm the source order.' : 'Confirm source grounding.')}</p>}
                    {resource.provenance.topic_map_status === 'REVIEW_REQUIRED' && !extracted.requires_review && <p className="mt-1 text-[9px] text-amber-100">The overall extraction needs review; verify this candidate against the full source.</p>}
                    {approved ? <div className="mt-2 flex items-center gap-1 text-[9px] text-emerald-200"><Check size={11} />Teacher approved</div> : <>
                      <label className="mt-2 block text-[9px] text-slate-400">Approved topic title<input value={draft.title} onChange={(event) => setTopicDrafts((current) => ({ ...current, [key]: { ...draft, title: event.target.value } }))} maxLength={200} className="form-input" /></label>
                      <label className="mt-2 block text-[9px] text-slate-400">Teacher-reviewed summary<textarea value={draft.summary} onChange={(event) => setTopicDrafts((current) => ({ ...current, [key]: { ...draft, summary: event.target.value } }))} rows={2} maxLength={5000} className="form-input resize-y" placeholder="A source-supported summary of this topic." /></label>
                      <label className="mt-2 block text-[9px] text-slate-400">Learning objectives · one per line<textarea value={draft.learning_objectives} onChange={(event) => setTopicDrafts((current) => ({ ...current, [key]: { ...draft, learning_objectives: event.target.value } }))} rows={2} className="form-input resize-y" /></label>
                      <label className="mt-2 block text-[9px] text-slate-400">Key concepts<input value={draft.concepts} onChange={(event) => setTopicDrafts((current) => ({ ...current, [key]: { ...draft, concepts: event.target.value } }))} className="form-input" /></label>
                      <button type="button" disabled={busy !== ''} onClick={() => void approveTopic(resource, extracted, index)} className="mt-2 inline-flex min-h-8 items-center gap-1.5 rounded-md border border-emerald-300/20 px-2.5 text-[10px] text-emerald-100 disabled:opacity-50"><Check size={11} />{busy === `approve-${key}` ? 'Approving…' : 'Approve topic'}</button>
                    </>}
                    {hasStudent && approved && approvedTopic && <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-white/[.06] pt-3">
                      {!assignment ? <><label className="w-24 text-[9px] text-slate-400">Study duration in minutes<input type="number" min={5} max={240} value={durations[approvedTopic.id] ?? 30} onChange={(event) => setDurations((current) => ({ ...current, [approvedTopic.id]: Number(event.target.value) }))} className="form-input" /></label><button type="button" disabled={busy !== ''} onClick={() => void assignTopic(approvedTopic)} className="min-h-9 rounded-md bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:opacity-50">{busy.startsWith('assign-') ? 'Assigning…' : `Assign to student #${studentId}`}</button></> : <p className="text-[10px] text-slate-400">Assigned to {assignment.student_name} · {assignment.expected_duration_minutes} min · {assignment.status.replaceAll('_', ' ')}</p>}
                    </div>}
                    {extracted.source_excerpt && <details className="mt-2"><summary className="cursor-pointer text-[9px] text-slate-500">Candidate source evidence</summary><blockquote className="mt-1 border-l border-white/10 pl-2 text-[10px] text-slate-400">{extracted.source_excerpt}</blockquote></details>}
                  </div>;
                })}</div>}
              </div>)}
              {!!moduleTopics.length && <div><h4 className="mb-2 text-[9px] font-semibold uppercase text-slate-500">Approved topic sequence</h4><ol className="space-y-1">{moduleTopics.map((topic, index) => <li key={topic.id} className="flex items-center gap-2 text-[10px] text-slate-300"><span className="grid size-5 place-items-center rounded-full border border-white/10 text-[9px] text-slate-500">{index + 1}</span>{topic.title}<span className="ml-auto text-[9px] text-slate-600">{topic.approval_status}</span></li>)}</ol></div>}
              {!!moduleAssignments.length && <div className="space-y-3"><h4 className="border-t border-white/[.07] pt-3 text-[9px] font-semibold uppercase text-slate-500">Student recovery progress</h4>{moduleAssignments.map((assignment) => <div key={assignment.id} className="rounded-lg border border-white/[.08] p-3">
                <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-[10px] font-medium text-slate-200">{assignment.topic_title}</p><p className="mt-1 text-[9px] text-slate-500">{assignment.expected_duration_minutes} minute timer · {assignment.attempt_count} attempt(s)</p></div><span className="rounded border border-white/10 px-2 py-1 text-[9px] text-slate-300">{assignment.latest_attempt?.review_pending ? 'AWAITING REVIEW' : assignment.latest_attempt?.percentage != null ? `${assignment.latest_attempt.percentage}%` : assignment.status.replaceAll('_', ' ')}</span></div>
                {resources.filter((resource) => resource.module_id === assignment.module_id).map((resource) => <button key={resource.id} type="button" onClick={() => void download(assignment, resource)} className="mt-2 inline-flex min-h-8 items-center gap-1.5 rounded border border-white/10 px-2 text-[9px] text-slate-300 hover:border-rose-300/20"><Download size={11} />{resource.original_filename}</button>)}
                {(!assignment.assessments.some((item) => item.status === 'PUBLISHED') || (assignment.status === 'REVISION_REQUIRED' && (assignment.latest_attempt?.percentage ?? 100) < 80)) && <button type="button" disabled={busy !== ''} onClick={() => void generateAssessment(assignment)} className="mt-3 inline-flex min-h-8 items-center gap-1.5 rounded-md border border-rose-300/20 px-2.5 text-[10px] text-rose-100 disabled:opacity-50">{busy === `generate-${assignment.id}` ? <LoaderCircle size={11} className="animate-spin" /> : <Plus size={11} />}{assignment.status === 'REVISION_REQUIRED' ? 'Generate revision assessment' : 'Generate assessment'}</button>}
                {!!(assessments[assignment.id] ?? []).length && <div className="mt-3 space-y-3">{(assessments[assignment.id] ?? []).map((assessment) => <div key={assessment.id} className="rounded-md border border-white/[.07] bg-black/10 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-[10px] font-medium text-slate-200">Assessment v{assessment.version} · {assessment.question_count}/20 questions</p><span className="text-[9px] text-amber-200">{assessment.status.replaceAll('_', ' ')}</span></div>
                  {assessment.error_message && <p role="alert" className="mt-2 text-[10px] text-rose-200">{assessment.error_message}</p>}
                  {!!assessment.validation_results.issues?.length && <ul className="mt-2 list-disc space-y-1 pl-4 text-[9px] text-rose-200">{assessment.validation_results.issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul>}
                  {!!assessment.questions.length && <details className="mt-2"><summary className="cursor-pointer text-[10px] text-slate-400">Review {assessment.questions.length} questions · 5 marks each · 100 total · keys, rubrics and source evidence</summary><ol className="mt-2 max-h-80 space-y-2 overflow-auto">{assessment.questions.map((question, index) => <li key={question.id} className="rounded border border-white/[.06] p-2 text-[9px]"><p className="font-medium text-slate-200">{index + 1}. [{question.question_type} · {question.marks} marks] {question.question}</p><p className="mt-1 text-slate-400">Key/reference: {question.correct_answer}{question.options.length ? ` · options: ${question.options.join(' | ')}` : ''}</p>{question.question_type === 'SHORT_ANSWER' && <p className="mt-1 text-slate-500">Rubric: {JSON.stringify(question.rubric)}</p>}<p className="mt-1 text-slate-600">Evidence: {question.source_evidence.map((item) => `${item.ref}: “${item.quote}”`).join(' · ')}</p></li>)}</ol></details>}
                  {assessment.source_evidence.length > 0 && <p className="mt-2 text-[9px] text-slate-600">Retrieved only from {Array.from(new Set(assessment.source_evidence.map((item) => item.source))).join(', ')}.</p>}
                  {assessment.status === 'REVIEW_REQUIRED' && <div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={!assessment.validation_results.valid || busy !== ''} onClick={() => void assessmentAction(assessment, 'approve', assignment.id)} className="min-h-8 rounded-md bg-emerald-700 px-3 text-[10px] text-white disabled:opacity-40">Approve validated draft</button><label className="min-w-36 flex-1 text-[9px] text-slate-500">Reason<input value={reviewReasons[assessment.id] ?? ''} onChange={(event) => setReviewReasons((current) => ({ ...current, [assessment.id]: event.target.value }))} className="form-input" /></label><button type="button" disabled={busy !== ''} onClick={() => void assessmentAction(assessment, 'reject', assignment.id)} className="min-h-8 rounded-md border border-rose-300/20 px-3 text-[10px] text-rose-100 disabled:opacity-50">Reject</button></div>}
                  {assessment.status === 'APPROVED' && <button type="button" disabled={busy !== ''} onClick={() => void assessmentAction(assessment, 'publish', assignment.id)} className="mt-3 min-h-8 rounded-md bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:opacity-50">Publish to student</button>}
                </div>)}</div>}
                {assignment.latest_attempt?.review_pending && <button type="button" disabled={busy !== ''} onClick={() => void openAttemptReview(assignment)} className="mt-3 min-h-8 rounded-md border border-amber-300/20 px-3 text-[10px] text-amber-100 disabled:opacity-50">{busy === `attempt-${assignment.id}` ? 'Loading responses…' : 'Review uncertain answers'}</button>}
                {attemptReviews[assignment.id] && <div className="mt-3 space-y-2 border-t border-white/[.07] pt-3"><p className="text-[10px] font-semibold text-amber-100">{attemptReviews[assignment.id].student_name} · {attemptReviews[assignment.id].percentage}% provisional</p>{attemptReviews[assignment.id].questions.filter((question) => question.answer?.review_required).map((question) => <div key={question.id} className="rounded border border-amber-300/10 p-2 text-[9px]"><p className="text-slate-200">{question.question} · {question.marks} marks</p><p className="mt-1 whitespace-pre-wrap text-slate-400">Student: {question.answer?.response_text}</p><p className="mt-1 text-slate-500">AI signal: {question.answer?.feedback} · {question.answer?.review_reason}</p><div className="mt-2 flex flex-wrap items-end gap-2"><label className="w-24 text-slate-500">Override marks<input type="number" min={0} max={question.marks} step={0.1} value={overrideMarks[`${assignment.id}:${question.id}`] ?? question.answer?.marks_awarded ?? 0} onChange={(event) => setOverrideMarks((current) => ({ ...current, [`${assignment.id}:${question.id}`]: Number(event.target.value) }))} className="form-input" /></label><label className="min-w-40 flex-1 text-slate-500">Override reason<input value={overrideReasons[`${assignment.id}:${question.id}`] ?? ''} onChange={(event) => setOverrideReasons((current) => ({ ...current, [`${assignment.id}:${question.id}`]: event.target.value }))} className="form-input" /></label><button type="button" disabled={busy !== ''} onClick={() => void reviewAnswer(assignment, question.id, 'override')} className="min-h-8 rounded bg-amber-700 px-3 text-[9px] text-white disabled:opacity-50">Record override</button><button type="button" disabled={busy !== ''} onClick={() => void reviewAnswer(assignment, question.id, 'accept')} className="min-h-8 rounded border border-white/10 px-3 text-[9px] text-slate-200 disabled:opacity-50">Accept current grade</button></div></div>)}</div>}
              </div>)}</div>}
            </div>}
          </article>;
        })}
      </div>}
    </div>
  </section>;
}
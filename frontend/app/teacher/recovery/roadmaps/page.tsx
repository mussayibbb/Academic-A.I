'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { Check, ChevronRight, CircleAlert, ClipboardCheck, Plus, RefreshCw, Save, Send, ShieldCheck, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Empty, ErrorNotice, Metric, PageHeading } from '@/components/ui';
import SubjectModuleRecoveryPanel from '@/components/SubjectModuleRecoveryPanel';
import { api } from '@/lib/api';

type AssignedStudent = { id: number; student_id: string; full_name: string; section: string | null };
type EvidenceRecord = { ref: string; kind: string; subject: string; [key: string]: unknown };
type SubjectEvidence = {
  id: number;
  code: string;
  name: string;
  fa1: number | null;
  fa1_observed_at: string | null;
  fa2: number | null;
  fa2_observed_at: string | null;
  assignment_mark: number | null;
  assignment_observed_at: string | null;
  attendance_rate: number | null;
  attendance_observed_at: string | null;
};
type FirstInternalEvidence = {
  evidence_id: string;
  mark_id: number | null;
  student_id: number | null;
  assessment_id: number | null;
  assessment: string;
  score: number | null;
  maximum_marks: number | null;
  maximum_marks_verified: boolean;
  normalized_score: number | null;
  recorded_at: string | null;
  official_result: string | null;
  evidence_status: string;
  reason: string | null;
  source_filenames: string[];
};
type SubjectFocusEvidence = {
  id: number;
  code: string;
  name: string;
  students: {
    id: number;
    status: 'LOW' | 'NOT_LOW' | 'INSUFFICIENT_EVIDENCE';
    reason: string;
    support_triggers: string[];
    official_failure_status: string | null;
    latest_assessments: FirstInternalEvidence[];
  }[];
};
type BlueprintTopic = {
  topic_id: number;
  title: string;
  summary: string;
  learning_objectives: string[];
  source_material: { ref: string; filename: string; title: string } | null;
  source_ready: boolean;
  source_provenance: Record<string, unknown>;
  syllabus_mapping: string | null;
  syllabus_mapping_status: string;
  conflict_resolution: string | null;
  conflict_resolution_status: string;
  decision: 'PENDING' | 'INCLUDE' | 'EXCLUDE';
  priority: 'HIGH' | 'MEDIUM' | 'LOW' | null;
  suggested_minutes: number | null;
  assignment_id: number | null;
  assessment: { status: string; question_count?: number; mcq_count?: number; short_answer_count?: number; validation_results?: { issues?: string[] } };
};
type BlueprintScheduleEntry = {
  scheduled_date: string;
  activity: 'TOPIC_STUDY' | 'REVISION' | 'MOCK_PRACTICE' | 'RETENTION_CHECK';
  stage?: 'MODULE_3' | 'MODULE_4' | 'REVISION_ASSESSMENT';
  estimated_minutes: number;
  topic_id: number | null;
  subject_id?: number | null;
};
type RecoveryBlueprint = {
  status: string;
  generation_status: string;
  academic_period: string | null;
  exam_date: string;
  available_minutes_per_day: number;
  syllabus: { context: string | null; source: string | null; status: string; reviewed: boolean };
  subjects: {
    subject_id: number;
    subject_name: string;
    focus_reason: string;
    eligibility: {
      eligible: boolean;
      status: string;
      support_triggers: string[];
      official_failure_status: string | null;
      reason: string;
      latest_evidence: FirstInternalEvidence[];
    };
    evidence_source: string;
    fa1_evidence: {
      academic_period: string | null;
      period_linkage: string;
      records: FirstInternalEvidence[];
    };
    topics: BlueprintTopic[];
  }[];
  schedule: BlueprintScheduleEntry[];
  blockers: string[];
};
type TaskActivity = {
  reason: string;
  lesson: string;
  steps: string[];
  exercise: string;
  submission_format: string;
};
type TaskDefinition = {
  task_key: string;
  title: string;
  learning_objective: string;
  instructions: string;
  activity_type: string;
  priority: 'LOW' | 'MEDIUM' | 'HIGH';
  subject: string | null;
  topic: string | null;
  stage?: 'MODULE_3' | 'MODULE_4' | 'REVISION_ASSESSMENT' | null;
  scheduled_at: string | null;
  deadline: string | null;
  estimated_minutes: number;
  resources: { title: string; url: string | null }[];
  completion_criteria: string;
  mastery_criteria: string | null;
  evidence_refs: string[];
  field_provenance: Record<string, string>;
  activity?: TaskActivity | null;
  status?: string;
  source?: string;
};
type RecoveryPlan = {
  id: number;
  student_id: number;
  version: number;
  status: 'DRAFT' | 'APPROVED' | 'PUBLISHED' | 'REJECTED';
  summary: string | null;
  plan: { tasks: TaskDefinition[]; blueprint?: RecoveryBlueprint };
  evidence: {
    academic_records: EvidenceRecord[];
    model_outputs: { risk_prediction: Record<string, unknown> | null };
    missing_information: string[];
    generation: Record<string, string>;
  };
};
type MonitoringTask = {
  id: number;
  student_id: number;
  subject_id: number | null;
  student_name: string;
  student_code: string;
  title: string;
  learning_objective: string;
  instructions: string;
  subject: string | null;
  completion_criteria: string;
  status: string;
  progress_percent: number;
  deadline: string | null;
  student_response: string | null;
  evidence: EvidenceRecord[];
  evidence_refs: string[];
  field_provenance: Record<string, string>;
  resources: { title: string; url: string | null }[];
  activity: TaskActivity;
  pending_activity: {
    title: string;
    learning_objective: string;
    instructions: string;
    completion_criteria: string;
    resources: { title: string; url: string | null }[];
    activity: MonitoringTask['activity'];
  } | null;
};
type Monitoring = {
  counts: Record<string, number>;
  tasks: MonitoringTask[];
  students: AssignedStudent[];
};
type FeedbackDraft = { feedback: string; score: string; decision: 'complete' | 'needs_revision' };
type ActivityEditor = {
  title: string;
  learning_objective: string;
  instructions: string;
  completion_criteria: string;
  reason: string;
  lesson: string;
  steps: string;
  exercise: string;
  submission_format: string;
};

const emptyMonitoring: Monitoring = { counts: {}, tasks: [], students: [] };

function localDateTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function asIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

function editPayload(task: TaskDefinition) {
  return {
    title: task.title,
    learning_objective: task.learning_objective,
    instructions: task.instructions,
    activity_type: task.activity_type,
    priority: task.priority,
    subject: task.subject,
    topic: task.topic,
    scheduled_at: task.scheduled_at,
    deadline: task.deadline,
    estimated_minutes: task.estimated_minutes,
    resources: task.resources,
    completion_criteria: task.completion_criteria,
    mastery_criteria: task.mastery_criteria,
    evidence_refs: task.evidence_refs,
    activity: task.activity ?? null,
  };
}

export default function TeacherRecoveryPage() {
  const [monitoring, setMonitoring] = useState<Monitoring>(emptyMonitoring);
  const [selectedStudentId, setSelectedStudentId] = useState<number | ''>('');
  const [plans, setPlans] = useState<RecoveryPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<number | ''>('');
  const [summary, setSummary] = useState('');
  const [draftTasks, setDraftTasks] = useState<TaskDefinition[]>([]);
  const [rejectionReason, setRejectionReason] = useState('');
  const [feedback, setFeedback] = useState<Record<number, FeedbackDraft>>({});
  const [activityEditors, setActivityEditors] = useState<Record<number, ActivityEditor>>({});
  const [academicPeriods, setAcademicPeriods] = useState<string[]>([]);
  const [academicPeriod, setAcademicPeriod] = useState('');
  const [studentSubjects, setStudentSubjects] = useState<SubjectEvidence[]>([]);
  const [subjectFocusEvidence, setSubjectFocusEvidence] = useState<SubjectFocusEvidence[]>([]);
  const [focusEvidenceLoading, setFocusEvidenceLoading] = useState(false);
  const [focusEvidenceError, setFocusEvidenceError] = useState('');
  const [focusSubjectIds, setFocusSubjectIds] = useState<number[]>([]);
  const [examDate, setExamDate] = useState('');
  const [syllabusContext, setSyllabusContext] = useState('');
  const [syllabusSource, setSyllabusSource] = useState('');
  const [noSyllabusAvailable, setNoSyllabusAvailable] = useState(false);
  const [availableMinutes, setAvailableMinutes] = useState(60);
  const [blueprintDraft, setBlueprintDraft] = useState<RecoveryBlueprint | null>(null);
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const reduceMotion = useReducedMotion() ?? false;

  const selectedPlan = useMemo(
    () => plans.find((item) => item.id === selectedPlanId) ?? null,
    [plans, selectedPlanId],
  );
  const selectedStudent = monitoring.students.find((item) => item.id === selectedStudentId) ?? null;

  const loadMonitoring = useCallback(async () => {
    const result = await api<Monitoring>('/api/v1/teacher/recovery-monitoring');
    setMonitoring(result);
    const requestedValue = typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('student_id');
    const requestedStudent = requestedValue ? Number(requestedValue) : null;
    const requestedIsAssigned = Number.isInteger(requestedStudent)
      && result.students.some((student) => student.id === requestedStudent);
    setSelectedStudentId((current) => {
      if (current !== '' && result.students.some((student) => student.id === current)) return current;
      if (requestedStudent !== null && Number.isInteger(requestedStudent)) {
        return requestedIsAssigned ? requestedStudent : '';
      }
      return result.students[0]?.id ?? '';
    });
    if (requestedStudent !== null && Number.isInteger(requestedStudent) && !requestedIsAssigned) {
      setError('The selected student is no longer in your assigned classes.');
    }
    return result;
  }, []);

  const loadPlans = useCallback(async () => {
    if (!selectedStudentId) {
      setPlans([]);
      setSelectedPlanId('');
      return;
    }
    const result = await api<RecoveryPlan[]>(`/api/v1/students/${selectedStudentId}/recovery-plans`);
    setPlans(result);
    setSelectedPlanId((current) => (
      current !== '' && result.some((plan) => plan.id === current)
        ? current
        : result[0]?.id ?? ''
    ));
  }, [selectedStudentId]);

  useEffect(() => {
    setLoading(true);
    loadMonitoring()
      .catch((requestError: Error) => setError(requestError.message))
      .finally(() => setLoading(false));
  }, [loadMonitoring]);

  useEffect(() => {
    setError('');
    loadPlans().catch((requestError: Error) => setError(requestError.message));
  }, [loadPlans]);

  useEffect(() => {
    if (!selectedStudentId) {
      setStudentSubjects([]);
      setFocusSubjectIds([]);
      setSubjectFocusEvidence([]);
      return;
    }
    let active = true;
    const params = academicPeriod ? `?academic_period=${encodeURIComponent(academicPeriod)}` : '';
    api<{ academic_period: string | null; academic_periods: string[]; items: { id: number; subjects: SubjectEvidence[] }[] }>(
      `/api/v1/teacher/student-risk-overview${params}`,
    ).then((result) => {
      if (!active) return;
      setAcademicPeriods(result.academic_periods);
      if (!academicPeriod && result.academic_period) setAcademicPeriod(result.academic_period);
      const record = result.items.find((item) => item.id === selectedStudentId);
      const subjects = record?.subjects ?? [];
      setStudentSubjects(subjects);
      const requestedSubjectValue = typeof window === 'undefined'
        ? null
        : new URLSearchParams(window.location.search).get('subject_id');
      const requestedSubjectId = requestedSubjectValue ? Number(requestedSubjectValue) : null;
      setFocusSubjectIds((current) => {
        if (requestedSubjectId && Number.isInteger(requestedSubjectId) && subjects.some((subject) => subject.id === requestedSubjectId)) {
          return [requestedSubjectId];
        }
        return current.filter((id) => subjects.some((subject) => subject.id === id));
      });
    }).catch((requestError: Error) => {
      if (active) setError(requestError.message);
    });
    return () => { active = false; };
  }, [selectedStudentId, academicPeriod]);

  useEffect(() => {
    if (!selectedStudentId) {
      setSubjectFocusEvidence([]);
      return;
    }
    let active = true;
    setFocusEvidenceLoading(true);
    setFocusEvidenceError('');
    const query = new URLSearchParams({
      student_id: String(selectedStudentId),
      first_internal_only: 'true',
    });
    api<{ subjects: SubjectFocusEvidence[] }>(`/api/v1/teacher/subject-focus?${query}`)
      .then((result) => {
        if (active) setSubjectFocusEvidence(result.subjects);
      })
      .catch((requestError: Error) => {
        if (active) setFocusEvidenceError(requestError.message);
      })
      .finally(() => {
        if (active) setFocusEvidenceLoading(false);
      });
    return () => { active = false; };
  }, [selectedStudentId]);

  useEffect(() => {
    if (!selectedPlan) {
      setSummary('');
      setDraftTasks([]);
      setBlueprintDraft(null);
      return;
    }
    setSummary(selectedPlan.summary ?? '');
    setDraftTasks(selectedPlan.plan.tasks ?? []);
    setBlueprintDraft(selectedPlan.plan.blueprint ?? null);
  }, [selectedPlan]);

  const refreshPlans = async (preferredId?: number) => {
    if (!selectedStudentId) return;
    const result = await api<RecoveryPlan[]>(`/api/v1/students/${selectedStudentId}/recovery-plans`);
    setPlans(result);
    const nextId = preferredId ?? result[0]?.id ?? '';
    setSelectedPlanId(nextId);
  };

  const generatePlan = async () => {
    if (!selectedStudent) return;
    setBusy('generate');
    setError('');
    setNotice('');
    try {
      const created = await api<RecoveryPlan>(`/api/v1/students/${selectedStudent.id}/recovery-plans`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      await refreshPlans(created.id);
      await loadMonitoring();
      setNotice(`Draft roadmap v${created.version} generated for ${selectedStudent.full_name}. Review evidence and task details before approval.`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const generateBlueprint = async () => {
    if (!selectedStudent) return;
    if (!examDate) {
      setError('Choose the official FA-2 date.');
      return;
    }
    if (!syllabusContext.trim() && !noSyllabusAvailable) {
      setError('Add the official syllabus context or explicitly confirm that no official mapping is available.');
      return;
    }
    if (syllabusContext.trim() && !syllabusSource.trim()) {
      setError('Provide the source label for the syllabus context.');
      return;
    }
    setBusy('blueprint');
    setError('');
    setNotice('');
    try {
      const created = await api<RecoveryPlan>(`/api/v1/students/${selectedStudent.id}/recovery-blueprints`, {
        method: 'POST',
        body: JSON.stringify({
          academic_period: academicPeriod || null,
          exam_date: examDate,
          syllabus_context: syllabusContext.trim() || null,
          syllabus_source: syllabusSource.trim() || null,
          acknowledge_no_syllabus: noSyllabusAvailable,
          available_minutes_per_day: availableMinutes,
        }),
      });
      await refreshPlans(created.id);
      setNotice(`Blueprint v${created.version} saved as a teacher-only draft. Review extracted scope, assessments, and schedule before approval.`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const updateBlueprintTopic = (topicId: number, values: Partial<BlueprintTopic>) => {
    setBlueprintDraft((current) => current ? ({
      ...current,
      subjects: current.subjects.map((subject) => ({
        ...subject,
        topics: subject.topics.map((topic) => topic.topic_id === topicId ? { ...topic, ...values } : topic),
      })),
    }) : current);
  };

  const saveBlueprint = async () => {
    if (!selectedPlan || !blueprintDraft) return;
    setBusy('blueprint-save');
    setError('');
    setNotice('');
    try {
      const topicItems = blueprintDraft.subjects.flatMap((subject) => subject.topics);
      const updated = await api<RecoveryPlan>(`/api/v1/recovery-plans/${selectedPlan.id}/blueprint`, {
        method: 'PATCH',
        body: JSON.stringify({
          topic_decisions: topicItems.filter((topic) => topic.decision !== 'PENDING').map((topic) => ({
            topic_id: topic.topic_id,
            decision: topic.decision,
          })),
          syllabus_mappings: topicItems.filter((topic) => topic.syllabus_mapping?.trim()).map((topic) => ({
            topic_id: topic.topic_id,
            mapping: topic.syllabus_mapping,
          })),
          conflict_resolutions: topicItems.filter((topic) => topic.conflict_resolution?.trim()).map((topic) => ({
            topic_id: topic.topic_id,
            resolution: topic.conflict_resolution,
          })),
          syllabus_reviewed: blueprintDraft.syllabus.reviewed,
          schedule: blueprintDraft.schedule.map((item) => ({
            scheduled_date: item.scheduled_date,
            activity: item.activity,
            stage: item.stage,
            estimated_minutes: item.estimated_minutes,
            ...(item.topic_id ? { topic_id: item.topic_id } : {}),
          })),
        }),
      });
      await refreshPlans(updated.id);
      setNotice('Blueprint review saved. Refresh assessment status after approving and publishing each assessment.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const refreshBlueprintAssessments = async () => {
    if (!selectedPlan || !blueprintDraft) return;
    setBusy('blueprint-refresh');
    setError('');
    try {
      const updated = await api<RecoveryPlan>(`/api/v1/recovery-plans/${selectedPlan.id}/blueprint`, {
        method: 'PATCH',
        body: JSON.stringify({ syllabus_reviewed: blueprintDraft.syllabus.reviewed }),
      });
      await refreshPlans(updated.id);
      setNotice('Assessment statuses refreshed from persisted assignments and published assessments.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const createBlueprintRevision = async () => {
    if (!selectedPlan) return;
    setBusy('revision');
    setError('');
    setNotice('');
    try {
      const revised = await api<RecoveryPlan>(`/api/v1/recovery-plans/${selectedPlan.id}/blueprint/revisions`, {
        method: 'POST',
      });
      await refreshPlans(revised.id);
      setNotice(`Created blueprint v${revised.version} for teacher review. Existing assignments, completed work, attempts, and grades were preserved.`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const saveDraft = async () => {
    if (!selectedPlan) return;
    if (draftTasks.some((task) => !task.title.trim() || !task.learning_objective.trim() || !task.instructions.trim() || !task.completion_criteria.trim() || task.estimated_minutes < 1)) {
      setError('Each task needs a title, objective, instructions, completion criteria, and a positive time estimate.');
      return;
    }
    setBusy('save');
    setError('');
    setNotice('');
    try {
      const updated = await api<RecoveryPlan>(`/api/v1/recovery-plans/${selectedPlan.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ summary: summary.trim(), tasks: draftTasks.map(editPayload) }),
      });
      await refreshPlans(updated.id);
      setNotice('Draft edits saved. Review again before approving.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const planAction = async (action: 'approve' | 'reject' | 'publish') => {
    if (!selectedPlan) return;
    if (action === 'reject' && !rejectionReason.trim()) {
      setError('Enter a reason before rejecting this roadmap.');
      return;
    }
    setBusy(action);
    setError('');
    setNotice('');
    try {
      const updated = await api<RecoveryPlan>(`/api/v1/recovery-plans/${selectedPlan.id}/${action}`, {
        method: 'POST',
        body: JSON.stringify(action === 'reject' ? { reason: rejectionReason.trim() } : {}),
      });
      await refreshPlans(updated.id);
      await loadMonitoring();
      setNotice(`Roadmap ${action === 'approve' ? 'approved' : action === 'reject' ? 'rejected' : 'published'}.`);
      if (action === 'reject') setRejectionReason('');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const assessTask = async (task: MonitoringTask) => {
    const current = feedback[task.id] ?? { feedback: '', score: '', decision: 'complete' as const };
    if (!current.feedback.trim()) {
      setError('Write feedback before submitting an assessment.');
      return;
    }
    setBusy(`assess-${task.id}`);
    setError('');
    setNotice('');
    try {
      await api(`/api/v1/recovery-tasks/${task.id}/assess`, {
        method: 'PATCH',
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          decision: current.decision,
          feedback: current.feedback.trim(),
          ...(current.score.trim() ? { score: Number(current.score) } : {}),
        }),
      });
      await loadMonitoring();
      setNotice(`Feedback saved for ${task.student_name}.`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const editActivity = (task: MonitoringTask) => {
    const content = task.pending_activity ?? {
      title: task.title,
      learning_objective: task.learning_objective,
      instructions: task.instructions,
      completion_criteria: task.completion_criteria,
      activity: task.activity,
    };
    setActivityEditors((current) => ({
      ...current,
      [task.id]: {
        title: content.title,
        learning_objective: content.learning_objective,
        instructions: content.instructions,
        completion_criteria: content.completion_criteria,
        reason: content.activity.reason,
        lesson: content.activity.lesson,
        steps: content.activity.steps.join('\n'),
        exercise: content.activity.exercise,
        submission_format: content.activity.submission_format,
      },
    }));
  };

  const updateActivity = (taskId: number, field: keyof ActivityEditor, value: string) => {
    setActivityEditors((current) => ({
      ...current,
      [taskId]: { ...current[taskId], [field]: value },
    }));
  };

  const saveActivity = async (task: MonitoringTask) => {
    const editor = activityEditors[task.id];
    if (!editor) return;
    const steps = editor.steps.split('\n').map((step) => step.trim()).filter(Boolean);
    if (steps.length === 0 || !editor.title.trim() || !editor.learning_objective.trim() || !editor.lesson.trim() || !editor.exercise.trim()) {
      setError('Provide a title, objective, lesson, exercise, and at least one activity step.');
      return;
    }
    setBusy(`activity-${task.id}`);
    setError('');
    setNotice('');
    try {
      await api(`/api/v1/recovery-tasks/${task.id}/activity`, {
        method: 'PUT',
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          title: editor.title.trim(),
          learning_objective: editor.learning_objective.trim(),
          instructions: editor.instructions.trim(),
          completion_criteria: editor.completion_criteria.trim(),
          resources: task.pending_activity?.resources ?? task.resources,
          activity: {
            reason: editor.reason.trim(),
            lesson: editor.lesson.trim(),
            steps,
            exercise: editor.exercise.trim(),
            submission_format: editor.submission_format.trim(),
          },
        }),
      });
      setActivityEditors((current) => {
        const next = { ...current };
        delete next[task.id];
        return next;
      });
      await loadMonitoring();
      setNotice('Replacement activity saved and withheld from the student until you approve it.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const approveActivity = async (task: MonitoringTask) => {
    setBusy(`activity-${task.id}`);
    setError('');
    setNotice('');
    try {
      await api(`/api/v1/recovery-tasks/${task.id}/approve-activity`, {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      });
      await loadMonitoring();
      setNotice('Replacement activity approved and made available to the student.');
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy('');
    }
  };

  const updateTask = (taskKey: string, values: Partial<TaskDefinition>) => {
    setDraftTasks((current) => current.map((task) => task.task_key === taskKey ? { ...task, ...values } : task));
  };

  const addTask = () => {
    setDraftTasks((current) => [...current, {
      task_key: `teacher-${crypto.randomUUID()}`,
      title: '',
      learning_objective: '',
      instructions: '',
      activity_type: 'TEACHER_REVIEW',
      priority: 'MEDIUM',
      subject: null,
      topic: null,
      scheduled_at: null,
      deadline: null,
      estimated_minutes: 0,
      resources: [],
      completion_criteria: '',
      mastery_criteria: null,
      evidence_refs: [],
      field_provenance: {},
      source: 'TEACHER_AUTHORED',
      status: 'AVAILABLE',
    }]);
  };

  const count = (name: string) => monitoring.counts[name] ?? 0;
  const routeParams = typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
  const routeStudentIdValue = routeParams?.get('student_id');
  const routeSubjectIdValue = routeParams?.get('subject_id');
  const routeStudentId = routeStudentIdValue ? Number(routeStudentIdValue) : null;
  const routeSubjectId = routeSubjectIdValue ? Number(routeSubjectIdValue) : null;
  const hasSubjectScope = routeStudentId === selectedStudentId
    && Number.isInteger(routeSubjectId)
    && (routeSubjectId ?? 0) > 0;
  const scopedSubject = hasSubjectScope
    ? studentSubjects.find((subject) => subject.id === routeSubjectId) ?? null
    : null;
  const filteredTasks = monitoring.tasks.filter((task) => (
    (selectedStudentId === '' || task.student_id === selectedStudentId)
    && (!hasSubjectScope || task.subject_id === routeSubjectId)
  ));
  const overdueTasks = monitoring.tasks.filter((task) => (
    task.status !== 'COMPLETED' && task.deadline !== null && new Date(task.deadline).getTime() < Date.now()
  ));
  const overdueStudents = [...overdueTasks.reduce((groups, task) => {
    const studentTasks = groups.get(task.student_id) ?? [];
    studentTasks.push(task);
    groups.set(task.student_id, studentTasks);
    return groups;
  }, new Map<number, MonitoringTask[]>()).entries()].sort((left, right) => left[1][0].student_name.localeCompare(right[1][0].student_name));

  return <div className="fade-up space-y-5">
    <PageHeading
      eyebrow="Faculty review · evidence-linked"
      title="Roadmap builder and monitoring"
      description="Generate a draft from recorded academic evidence, review and edit tasks, then approve and publish. Students see only published tasks."
      action={<button type="button" onClick={() => { setLoading(true); loadMonitoring().catch((requestError: Error) => setError(requestError.message)).finally(() => setLoading(false)); }} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50"><RefreshCw size={13} />Refresh monitoring</button>}
    />
    {error && <ErrorNotice message={error} />}
    {notice && <div role="status" className="rounded-lg border border-rose-200/20 bg-rose-200/[.04] px-4 py-3 text-xs text-rose-100">{notice}</div>}

    <section aria-label="Recovery task monitoring" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
      <Metric label="Available" value={count('AVAILABLE')} detail="Published and not started" color="amber" />
      <Metric label="Teacher approval" value={count('DRAFT')} detail="Replacement activities withheld" color="rose" />
      <Metric label="In progress" value={count('IN_PROGRESS')} detail="Student work underway" color="rose" />
      <Metric label="Submitted" value={count('SUBMITTED')} detail="Awaiting teacher review" color="amber" />
      <Metric label="Needs revision" value={count('NEEDS_REVISION')} detail="Returned with feedback" color="rose" />
      <Metric label="Completed" value={count('COMPLETED')} detail="Recovery tasks assessed complete" color="mint" />
      <Metric label="Overdue" value={count('overdue')} detail="Has an explicit past deadline" color="rose" />
    </section>

    <section aria-label="Students with overdue recovery tasks" className="surface rounded-xl p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="eyebrow">Persisted outcomes · deadline-based</p><h2 className="mt-1 text-sm font-semibold text-white">Stalled recovery work</h2><p className="mt-1 text-[10px] text-slate-500">Only unfinished tasks with an explicit past deadline are flagged; no inactivity threshold is inferred.</p></div>
        <span className="rounded-full border border-rose-200/15 bg-rose-200/[.04] px-2.5 py-1 text-[10px] text-rose-100">{overdueTasks.length} overdue task{overdueTasks.length === 1 ? '' : 's'}</span>
      </div>
      {!overdueStudents.length
        ? <p className="mt-4 text-xs text-slate-500">No unfinished recovery tasks currently have a past deadline.</p>
        : <ul className="mt-4 grid gap-3 md:grid-cols-2">
          {overdueStudents.map(([studentId, tasks]) => <li key={studentId} className="rounded-lg border border-rose-200/10 bg-rose-200/[.02] p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div><p className="text-xs font-semibold text-white">{tasks[0].student_name}</p><p className="mt-1 font-mono text-[9px] text-slate-500">{tasks[0].student_code} · {tasks.length} overdue task{tasks.length === 1 ? '' : 's'}</p></div>
              <Link href={`/teacher/recovery/students/${studentId}`} className="inline-flex min-h-8 items-center gap-1 rounded-md border border-white/10 px-2.5 text-[9px] text-slate-200 hover:border-rose-200/20">Recovery profile <ChevronRight size={11} /></Link>
            </div>
            <ul className="mt-3 space-y-2">
              {tasks.map((task) => <li key={task.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[.05] pt-2">
                <div className="min-w-0"><p className="truncate text-[10px] text-slate-200">{task.title}</p><p className="mt-1 text-[9px] text-slate-500">{task.subject ?? 'Subject not specified'} · due {new Date(task.deadline as string).toLocaleDateString()}</p></div>
                {task.subject_id !== null && <div className="flex flex-wrap gap-2">
                  <Link href={`/teacher/recovery/subjects/${task.subject_id}/students/${studentId}`} className="text-[9px] font-medium text-rose-100 hover:text-white">Upload Modules</Link>
                  <Link href={`/teacher/recovery/roadmaps?student_id=${studentId}&subject_id=${task.subject_id}`} className="text-[9px] font-medium text-slate-300 hover:text-white">Individual Roadmap</Link>
                </div>}
              </li>)}
            </ul>
          </li>)}
        </ul>}
    </section>

    <section className="surface rounded-xl p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <label className="min-w-0 flex-1 text-xs text-slate-400">Assigned student
          <select value={selectedStudentId} onChange={(event) => setSelectedStudentId(event.target.value ? Number(event.target.value) : '')} className="form-input">
            {!monitoring.students.length && <option value="">No assigned students</option>}
            {monitoring.students.map((student) => <option key={student.id} value={student.id}>{student.full_name} · {student.student_id}{student.section ? ` · ${student.section}` : ''}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => void generatePlan()} disabled={!selectedStudent || busy !== ''} className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg border border-white/10 px-4 text-xs font-semibold text-slate-200 transition hover:border-rose-300/25 disabled:cursor-not-allowed disabled:opacity-50"><ClipboardCheck size={15} />{busy === 'generate' ? 'Generating…' : 'Generate evidence review plan'}</button>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-400">Academic term label (optional; not used to infer assessment dates)
          <select value={academicPeriod} onChange={(event) => setAcademicPeriod(event.target.value)} className="form-input">
            <option value="">Not specified</option>
            {academicPeriods.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-400">Official FA-2 exam date
          <input type="date" value={examDate} onChange={(event) => setExamDate(event.target.value)} className="form-input" />
        </label>
      </div>
      <section aria-label="First-internal eligibility evidence" className="mt-4 rounded-lg border border-white/[.07] p-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-medium text-slate-200">First-internal evidence for all assigned subjects</h3>
            <p className="mt-1 text-[10px] leading-4 text-slate-500">Roadmap eligibility requires dated FA-1 marks and an explicitly verified maximum below 12/25. FA-2 never substitutes; official FAIL is shown separately and does not qualify a subject by score.</p>
          </div>
          {focusEvidenceLoading && <span role="status" className="text-[10px] text-slate-500">Loading evidence…</span>}
        </div>
        {focusEvidenceError && <div className="mt-3"><ErrorNotice message={focusEvidenceError} /></div>}
        {!focusEvidenceLoading && !focusEvidenceError && subjectFocusEvidence.length === 0
          ? <p className="mt-3 text-[10px] text-slate-500">No subject evidence is available in your assigned teaching scope.</p>
          : <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {subjectFocusEvidence.map((subject) => {
              const evidence = subject.students.find((item) => item.id === selectedStudentId);
              const scoreEligible = evidence?.support_triggers.includes('LOW_SCORE') ?? false;
              const failEligible = evidence?.support_triggers.includes('OFFICIAL_FAIL') ?? false;
              return <article key={subject.id} className="rounded-md border border-white/[.06] p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="font-medium text-white">{subject.code} · {subject.name}</h4>
                  <span className={scoreEligible ? 'text-rose-200' : 'text-amber-200'}>
                    {scoreEligible ? 'Eligible: below 12/25' : failEligible ? 'Official FAIL · separate signal; not roadmap-eligible' : evidence?.status === 'NOT_LOW' ? 'Not below threshold' : 'Insufficient evidence'}
                  </span>
                </div>
                {evidence?.latest_assessments.length
                  ? <ul className="mt-2 space-y-2">{evidence.latest_assessments.map((item) => <li key={item.evidence_id} className="border-t border-white/[.05] pt-2 text-[10px] leading-4 text-slate-400">
                    <p>{item.assessment} · mark {item.mark_id ?? 'unknown'} · {item.recorded_at ? new Date(item.recorded_at).toLocaleString() : 'date missing'}</p>
                    <p>{item.score ?? '—'} / {item.maximum_marks ?? '—'} · {item.maximum_marks_verified ? 'maximum verified' : 'maximum unverified'} · normalized {item.normalized_score === null ? 'unavailable' : `${item.normalized_score.toFixed(2)}/25`}</p>
                    {item.source_filenames.length > 0 && <p>Source: {item.source_filenames.join(', ')}</p>}
                    {item.official_result && <p>Official result: {item.official_result} (separate from score classification)</p>}
                    {item.reason && <p className="text-amber-200">{item.reason}</p>}
                  </li>)}</ul>
                  : <p className="mt-2 text-[10px] text-slate-500">No uniquely identified, dated FA-1 assessment mark was found.</p>}
                {evidence && !scoreEligible && !failEligible && <p className="mt-2 text-[10px] leading-4 text-amber-200">{evidence.reason}</p>}
              </article>;
            })}
          </div>}
      </section>
      <fieldset className="mt-4 rounded-lg border border-white/[.07] p-3">
        <legend className="px-1 text-xs font-medium text-slate-200">Subject module review (independent of roadmap eligibility)</legend>
        {studentSubjects.length ? <div className="grid gap-2 sm:grid-cols-2">
          {studentSubjects.map((subject) => {
            const marks = [
              subject.fa1 !== null ? `FA-1 ${subject.fa1}` : null,
              subject.fa2 !== null ? `FA-2 ${subject.fa2}` : null,
              subject.assignment_mark !== null ? `Assignment ${subject.assignment_mark}` : null,
              subject.attendance_rate !== null ? `Attendance ${subject.attendance_rate}%` : null,
            ].filter(Boolean).join(' · ');
            return <label key={subject.id} className="flex min-w-0 items-start gap-2 rounded-md border border-white/[.06] p-3 text-xs">
              <input type="checkbox" checked={focusSubjectIds.includes(subject.id)} onChange={(event) => setFocusSubjectIds((current) => event.target.checked ? [...current, subject.id] : current.filter((id) => id !== subject.id))} className="mt-0.5 accent-rose-500" />
              <span className="min-w-0"><span className="font-medium text-white">{subject.code} · {subject.name}</span><span className="mt-1 block text-[10px] leading-4 text-slate-500">{marks || 'No marks or attendance recorded for this semester.'} · Select only to open its module files and topic review; roadmap subjects are derived from verified FA-1 evidence.</span></span>
            </label>;
          })}
        </div> : <p className="text-[10px] text-slate-500">No persisted subject records are available for this student and semester.</p>}
      </fieldset>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-400 sm:col-span-2">Official syllabus / FA-2 scope context
          <textarea rows={3} value={syllabusContext} onChange={(event) => setSyllabusContext(event.target.value)} placeholder="Paste or summarize teacher-supplied official syllabus scope; the AI may not invent mappings." className="form-input resize-y" />
        </label>
        {syllabusContext.trim() && <label className="text-xs text-slate-400 sm:col-span-2">Syllabus source / document label
          <input value={syllabusSource} onChange={(event) => setSyllabusSource(event.target.value)} className="form-input" placeholder="Official syllabus, course notice, document name…" />
        </label>}
        <label className="text-xs text-slate-400">Available study minutes per day
          <input type="number" min="5" max="600" value={availableMinutes} onChange={(event) => setAvailableMinutes(Number(event.target.value))} className="form-input" />
        </label>
        <label className="flex items-center gap-2 self-end pb-3 text-[10px] text-slate-400">
          <input type="checkbox" checked={noSyllabusAvailable} onChange={(event) => setNoSyllabusAvailable(event.target.checked)} className="accent-rose-500" />
          No official syllabus mapping is currently available
        </label>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/[.07] pt-4">
        <p className="max-w-2xl text-[10px] leading-5 text-slate-500">Blueprint generation only recommends from teacher-approved topics in readable uploaded sources. It creates hidden draft assignments for assessment review; nothing becomes student-visible until publication checks pass.</p>
        <button type="button" onClick={() => void generateBlueprint()} disabled={!selectedStudent || busy !== '' || focusEvidenceLoading} className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-50"><ClipboardCheck size={15} />{busy === 'blueprint' ? 'Generating blueprint…' : 'Generate AI roadmap blueprint'}</button>
      </div>
    </section>

    {focusSubjectIds.length ? focusSubjectIds.map((subjectId) => {
      const subject = studentSubjects.find((item) => item.id === subjectId);
      const reason = subject ? [
        'Teacher-confirmed focus from persisted records.',
        subject.fa1 !== null ? `FA-1: ${subject.fa1}` : null,
        subject.fa2 !== null ? `FA-2: ${subject.fa2}` : null,
        subject.assignment_mark !== null ? `Assignment: ${subject.assignment_mark}` : null,
        subject.attendance_rate !== null ? `Attendance: ${subject.attendance_rate}%` : null,
      ].filter(Boolean).join(' · ') : undefined;
      return <motion.div key={subjectId} initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : 0.24 }}>
        <SubjectModuleRecoveryPanel studentId={selectedStudent?.id ?? 0} initialSubjectId={subjectId} subjectFocusReason={reason} />
      </motion.div>;
    }) : <div className="surface rounded-xl p-4 text-xs text-slate-500">Confirm one or more subjects above to manage their module files and extracted topic reviews.</div>}

    {selectedPlan && <section className="surface overflow-hidden rounded-xl">
      <div className="flex flex-col gap-3 border-b border-white/[.07] p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
        <div><p className="eyebrow">Roadmap review · {selectedStudent?.full_name ?? `student ${selectedPlan.student_id}`}</p><h2 className="mt-1 text-base font-semibold text-white">Version {selectedPlan.version}</h2></div>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Select recovery roadmap version" value={selectedPlanId} onChange={(event) => setSelectedPlanId(Number(event.target.value))} className="h-10 rounded-lg border border-white/10 bg-[#17191c] px-3 text-xs text-slate-200">
            {plans.map((plan) => <option key={plan.id} value={plan.id}>v{plan.version} · {plan.status.toLowerCase()}</option>)}
          </select>
          <span className="rounded-full border border-rose-300/20 bg-rose-300/[.06] px-3 py-1.5 text-[10px] font-semibold text-rose-100">{selectedPlan.status}</span>
        </div>
      </div>

      <div className="space-y-4 p-4 sm:p-5">
        <label className="block text-xs text-slate-400">Plan summary
          <textarea rows={2} value={summary} onChange={(event) => setSummary(event.target.value)} disabled={selectedPlan.status !== 'DRAFT'} className="form-input resize-y disabled:opacity-70" />
        </label>

        <details className="rounded-lg border border-white/[.07] bg-black/10 p-3">
          <summary className="cursor-pointer text-xs font-medium text-slate-200">Evidence provenance and missing information</summary>
          {selectedPlan.evidence.academic_records.length
            ? <ul className="mt-3 space-y-2">{selectedPlan.evidence.academic_records.map((record) => <li key={record.ref} className="rounded-md border border-white/[.06] p-3 text-[11px]"><span className="font-medium text-white">{record.subject}</span><span className="ml-2 text-slate-500">{record.kind.replaceAll('_', ' ')}</span><pre className="mt-2 whitespace-pre-wrap break-words text-[10px] text-slate-400">{JSON.stringify(record, null, 2)}</pre></li>)}</ul>
            : <p className="mt-3 text-xs text-slate-500">No verified academic records were available when this draft was generated.</p>}
          {selectedPlan.evidence.model_outputs.risk_prediction && <p className="mt-3 text-[11px] text-slate-400">Persisted risk output: {String(selectedPlan.evidence.model_outputs.risk_prediction.risk_level)} · {String(selectedPlan.evidence.model_outputs.risk_prediction.model_version)}</p>}
          <ul className="mt-3 list-inside list-disc space-y-1 text-[10px] text-slate-500">{selectedPlan.evidence.missing_information.map((item) => <li key={item}>{item}</li>)}</ul>
        </details>

        {blueprintDraft && <section aria-label="AI recovery roadmap blueprint review" className="rounded-xl border border-rose-200/15 bg-rose-200/[.025] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="eyebrow">Proposed blueprint · teacher review required</p><h3 className="mt-1 text-sm font-semibold text-white">FA-2 by {new Date(`${blueprintDraft.exam_date}T00:00:00`).toLocaleDateString()}</h3><p className="mt-1 text-[10px] text-slate-500">{blueprintDraft.academic_period} · {blueprintDraft.generation_status.replaceAll('_', ' ').toLowerCase()} · {blueprintDraft.available_minutes_per_day} available minutes/day</p></div>
            <div className="flex flex-wrap gap-2">
              {selectedPlan.status === 'PUBLISHED' && <button type="button" onClick={() => void createBlueprintRevision()} disabled={busy !== ''} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-rose-200/20 px-3 text-[10px] text-rose-100 disabled:opacity-50"><Plus size={12} />{busy === 'revision' ? 'Creating revision…' : 'Revise future work'}</button>}
              {selectedPlan.status === 'DRAFT' && <button type="button" onClick={() => void refreshBlueprintAssessments()} disabled={busy !== ''} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-[10px] text-slate-200 disabled:opacity-50"><RefreshCw size={12} />{busy === 'blueprint-refresh' ? 'Refreshing…' : 'Refresh assessment status'}</button>}
              {selectedPlan.status === 'DRAFT' && <button type="button" onClick={() => void saveBlueprint()} disabled={busy !== ''} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-rose-600 px-3 text-[10px] font-semibold text-white disabled:opacity-50"><Save size={12} />{busy === 'blueprint-save' ? 'Saving…' : 'Save blueprint review'}</button>}
            </div>
          </div>
          <div className="mt-4 rounded-lg border border-white/[.07] p-3">
            <p className="text-[10px] font-medium text-slate-200">Syllabus context provenance</p>
            <p className="mt-1 text-[10px] leading-5 text-slate-400">{blueprintDraft.syllabus.source || 'No official syllabus source supplied'}{blueprintDraft.syllabus.context ? ` · ${blueprintDraft.syllabus.context}` : ' · No official topic mappings were supplied.'}</p>
            {selectedPlan.status === 'DRAFT' && <label className="mt-3 flex items-center gap-2 text-[10px] text-slate-300"><input type="checkbox" checked={blueprintDraft.syllabus.reviewed} onChange={(event) => setBlueprintDraft((current) => current ? { ...current, syllabus: { ...current.syllabus, reviewed: event.target.checked } } : current)} className="accent-rose-500" />I reviewed the syllabus context or confirmed no official mapping is available.</label>}
          </div>
          {blueprintDraft.blockers.length > 0 && <div className="mt-3 rounded-lg border border-amber-200/15 bg-amber-200/[.03] p-3"><p className="text-[10px] font-semibold text-amber-100">Missing evidence / unresolved issues</p><ul className="mt-2 list-inside list-disc space-y-1 text-[10px] text-amber-100/70">{blueprintDraft.blockers.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>}
          <div className="mt-4 space-y-3">
            {blueprintDraft.subjects.map((subject) => <article key={subject.subject_id} className="rounded-lg border border-white/[.07] bg-black/[.12] p-3">
              <h4 className="text-xs font-semibold text-white">{subject.subject_name}</h4>
              <p className="mt-1 text-[10px] leading-5 text-rose-100/80">{subject.focus_reason}</p>
              <div className="mt-2 rounded-md border border-white/[.05] p-2">
                <p className="text-[9px] font-medium text-slate-300">Authoritative FA-1 evidence · {subject.eligibility.status.replaceAll('_', ' ').toLowerCase()}</p>
                <p className="mt-1 text-[9px] text-slate-500">{subject.eligibility.reason} {subject.eligibility.official_failure_status ? `Official result: ${subject.eligibility.official_failure_status} (tracked separately from normalized score).` : 'No official FAIL recorded.'}</p>
                {subject.fa1_evidence.records.length
                  ? <ul className="mt-2 space-y-1">{subject.fa1_evidence.records.map((record) => <li key={record.evidence_id} className="text-[9px] text-slate-400">
                    {record.assessment} · {record.recorded_at ? new Date(record.recorded_at).toLocaleDateString() : 'date unavailable'} · Mark {record.mark_id ?? 'unknown'} · {record.score ?? 'unknown'} / {record.maximum_marks ?? 'unknown'}{record.maximum_marks_verified ? ' (maximum verified)' : ' (maximum unverified)'} · normalized {record.normalized_score ?? 'unavailable'} / 25
                    {record.source_filenames.length ? ` · Source: ${record.source_filenames.join(', ')}` : ''}
                    {record.reason ? ` · ${record.reason}` : ''}
                  </li>)}</ul>
                  : <p className="mt-1 text-[9px] text-amber-100">No dated FA-1 mark evidence is available for this subject.</p>}
                <p className="mt-1 text-[9px] text-slate-600">Assessment period: {subject.fa1_evidence.academic_period ?? 'not linked to a recorded academic period'} · {subject.fa1_evidence.period_linkage.replaceAll('_', ' ').toLowerCase()}</p>
              </div>
              {!subject.topics.length ? <p className="mt-3 text-[10px] text-amber-100">No approved extracted topics are available. Upload readable source files, map topics, and approve them in this subject’s module panel above.</p> : <div className="mt-3 space-y-2">
                {subject.topics.map((topic) => <div key={topic.topic_id} className="rounded-md border border-white/[.06] p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0"><p className="text-[11px] font-medium text-slate-100">{topic.title}</p><p className="mt-1 text-[10px] leading-5 text-slate-400">{topic.summary}</p>                    <p className="mt-1 text-[9px] text-slate-500">Source: {topic.source_material?.filename ?? 'No readable source'}{topic.source_material ? ` · ${topic.source_material.ref}` : ''} · Assessment: {topic.assessment.status.replaceAll('_', ' ').toLowerCase()}{topic.assessment.question_count !== undefined ? ` (${topic.assessment.mcq_count ?? 0} MCQ / ${topic.assessment.short_answer_count ?? 0} short answer)` : ''} · Planned completion: {blueprintDraft.schedule.filter((item) => item.topic_id === topic.topic_id).map((item) => item.scheduled_date).sort().at(-1) ?? 'not scheduled'}</p>
                    {!!topic.assessment.validation_results?.issues?.length && <ul className="mt-1 list-inside list-disc text-[9px] text-amber-100">{topic.assessment.validation_results.issues.map((issue, issueIndex) => <li key={`${issueIndex}-${issue}`}>{issue}</li>)}</ul>}</div>
                    {selectedPlan.status === 'DRAFT' ? <select aria-label={`Topic decision ${topic.title}`} value={topic.decision} onChange={(event) => updateBlueprintTopic(topic.topic_id, { decision: event.target.value as BlueprintTopic['decision'] })} className="h-9 rounded-md border border-white/10 bg-[#17191c] px-2 text-[10px] text-slate-200"><option value="PENDING">Choose decision</option><option value="INCLUDE">Include</option><option value="EXCLUDE">Exclude</option></select> : <span className="text-[9px] text-slate-400">{topic.decision}</span>}
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <label className="text-[9px] text-slate-500">Official syllabus mapping
                      <textarea rows={2} value={topic.syllabus_mapping ?? ''} onChange={(event) => updateBlueprintTopic(topic.topic_id, { syllabus_mapping: event.target.value || null })} disabled={selectedPlan.status !== 'DRAFT'} placeholder="Teacher-confirmed section/page/outcome, or leave unresolved." className="form-input resize-y text-[10px]" />
                    </label>
                    {topic.conflict_resolution_status === 'NEEDS_TEACHER_REVIEW' && <label className="text-[9px] text-amber-100">Source-order conflict resolution
                      <textarea rows={2} value={topic.conflict_resolution ?? ''} onChange={(event) => updateBlueprintTopic(topic.topic_id, { conflict_resolution: event.target.value || null })} disabled={selectedPlan.status !== 'DRAFT'} placeholder="Explain the teacher-confirmed ordering or resolution." className="form-input resize-y text-[10px]" />
                    </label>}
                  </div>
                  <p className="mt-2 text-[9px] text-slate-500">AI priority: {topic.priority ?? 'unavailable'} · pedagogical sequence only (no official exam weights supplied) · suggested study time: {topic.suggested_minutes ?? 'unavailable'} min · Dependencies: none specified in the approved source. Source-backed assessment must contain stored answer keys, rubrics, and mappings.</p>
                </div>)}
              </div>}
            </article>)}
          </div>
          <div className="mt-4 rounded-lg border border-white/[.07] p-3">
            <h4 className="text-[10px] font-semibold text-slate-200">Global staged plan · teacher-confirmed dates required</h4>
            <div className="mt-2 space-y-2">
              {blueprintDraft.schedule.map((item, index) => {
                const topic = blueprintDraft.subjects.flatMap((subject) => subject.topics).find((candidate) => candidate.topic_id === item.topic_id);
                return <div key={`${item.scheduled_date}-${item.activity}-${index}`} className="grid gap-2 rounded-md border border-white/[.05] p-2 sm:grid-cols-[1fr_1.3fr_110px] sm:items-center">
                  <label className="text-[9px] text-slate-500">Date<input type="date" value={item.scheduled_date} disabled={selectedPlan.status !== 'DRAFT'} onChange={(event) => setBlueprintDraft((current) => current ? { ...current, schedule: current.schedule.map((entry, entryIndex) => entryIndex === index ? { ...entry, scheduled_date: event.target.value } : entry) } : current)} className="form-input mt-1 text-[10px]" /></label>
                  <p className="text-[10px] text-slate-300">{item.stage === 'MODULE_3' ? 'Stage 1 · Module 3' : item.stage === 'MODULE_4' ? 'Stage 2 · Module 4' : 'Stage 3 · Revision / assessment practice'} · {item.activity.replaceAll('_', ' ')} · {topic?.title ?? 'All eligible subjects'} · Day total {blueprintDraft.schedule.filter((entry) => entry.scheduled_date === item.scheduled_date).reduce((total, entry) => total + entry.estimated_minutes, 0)} min</p>
                  <label className="text-[9px] text-slate-500">Minutes<input type="number" min="1" max="600" value={item.estimated_minutes} disabled={selectedPlan.status !== 'DRAFT'} onChange={(event) => setBlueprintDraft((current) => current ? { ...current, schedule: current.schedule.map((entry, entryIndex) => entryIndex === index ? { ...entry, estimated_minutes: Number(event.target.value) } : entry) } : current)} className="form-input mt-1 text-[10px]" /></label>
                </div>;
              })}
              {!blueprintDraft.schedule.length && <p className="text-[10px] text-amber-100">No feasible schedule has been generated. Add source-backed topics and retry after resolving missing evidence.</p>}
            </div>
          </div>
          <p className="mt-3 text-[9px] leading-4 text-slate-500">Provenance is separated: records are verified persisted facts; module topics are extracted and teacher-approved source content; priorities and durations are AI suggestions; mappings and decisions require teacher confirmation. Draft assignments and assessments stay hidden from students.</p>
        </section>}

        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-slate-100">Roadmap tasks</h3>{selectedPlan.status === 'DRAFT' && <button type="button" onClick={addTask} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25"><Plus size={13} />Add teacher-authored task</button>}</div>
        {draftTasks.length ? <div className="space-y-3">{draftTasks.map((task, index) => <motion.article key={task.task_key} initial={reduceMotion ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }} className="rounded-xl border border-white/[.08] bg-black/[.12] p-4">
          <div className="mb-3 flex items-center justify-between gap-3"><p className="text-[10px] font-semibold uppercase tracking-wider text-rose-200">Task {index + 1} · {task.subject ?? task.source ?? 'Teacher-authored'}</p>{selectedPlan.status === 'DRAFT' && <button type="button" aria-label={`Remove task ${index + 1}`} onClick={() => setDraftTasks((current) => current.filter((item) => item.task_key !== task.task_key))} className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-rose-300/10 hover:text-rose-100"><Trash2 size={14} /></button>}</div>
          {selectedPlan.status === 'DRAFT' ? <div className="grid gap-3 md:grid-cols-2">
            <label className="text-[10px] text-slate-500 md:col-span-2">Task title<input value={task.title} onChange={(event) => updateTask(task.task_key, { title: event.target.value })} className="form-input" /></label>
            <label className="text-[10px] text-slate-500 md:col-span-2">Learning objective<textarea rows={2} value={task.learning_objective} onChange={(event) => updateTask(task.task_key, { learning_objective: event.target.value })} className="form-input resize-y" /></label>
            <label className="text-[10px] text-slate-500 md:col-span-2">Instructions<textarea rows={3} value={task.instructions} onChange={(event) => updateTask(task.task_key, { instructions: event.target.value })} className="form-input resize-y" /></label>
            <label className="text-[10px] text-slate-500">Time estimate in minutes<input type="number" min="1" max="240" value={task.estimated_minutes || ''} onChange={(event) => updateTask(task.task_key, { estimated_minutes: Number(event.target.value) })} className="form-input" /></label>
            <label className="text-[10px] text-slate-500">Priority<select value={task.priority} onChange={(event) => updateTask(task.task_key, { priority: event.target.value as TaskDefinition['priority'] })} className="form-input"><option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option></select></label>
            <label className="text-[10px] text-slate-500">Schedule · optional<input type="datetime-local" value={localDateTime(task.scheduled_at)} onChange={(event) => updateTask(task.task_key, { scheduled_at: asIso(event.target.value) })} className="form-input" /></label>
            <label className="text-[10px] text-slate-500">Deadline · optional<input type="datetime-local" value={localDateTime(task.deadline)} onChange={(event) => updateTask(task.task_key, { deadline: asIso(event.target.value) })} className="form-input" /></label>
            <label className="text-[10px] text-slate-500 md:col-span-2">Completion criteria<textarea rows={2} value={task.completion_criteria} onChange={(event) => updateTask(task.task_key, { completion_criteria: event.target.value })} className="form-input resize-y" /></label>
          </div> : <div><h4 className="text-sm font-medium text-white">{task.title}</h4><p className="mt-2 text-xs leading-5 text-slate-300">{task.learning_objective}</p><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{task.instructions}</p><p className="mt-2 text-[10px] text-slate-500">{task.estimated_minutes} minutes · {task.scheduled_at ? new Date(task.scheduled_at).toLocaleString() : 'unscheduled'} · {task.deadline ? new Date(task.deadline).toLocaleString() : 'no deadline'}</p></div>}
          <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">Linked evidence and generated-field provenance</summary><p className="mt-2">Evidence references: {task.evidence_refs.length ? task.evidence_refs.join(', ') : 'none linked'}</p><ul className="mt-2 grid gap-1 sm:grid-cols-2">{Object.entries(task.field_provenance ?? {}).map(([field, value]) => <li key={field}>{field.replaceAll('_', ' ')}: {value.replaceAll('_', ' ').toLowerCase()}</li>)}</ul></details>
        </motion.article>)}</div> : <div className="rounded-lg border border-dashed border-white/10 p-5"><Empty title="No tasks in this draft" detail="Add a teacher-authored task, or reject this draft if the available evidence is insufficient." /></div>}

        {selectedPlan.status === 'DRAFT' && <div className="flex flex-wrap gap-2 border-t border-white/[.07] pt-4">
          <button type="button" disabled={busy !== ''} onClick={() => void saveDraft()} className="min-h-10 rounded-lg border border-white/10 px-4 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50">{busy === 'save' ? 'Saving…' : 'Save draft edits'}</button>
          <button type="button" disabled={busy !== '' || !draftTasks.length} onClick={() => void planAction('approve')} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"><ShieldCheck size={14} />Approve</button>
          <label className="min-w-48 flex-1 text-[10px] text-slate-500">Reason for rejection<input value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} className="form-input" placeholder="Required to reject" /></label>
          <button type="button" disabled={busy !== ''} onClick={() => void planAction('reject')} className="min-h-10 rounded-lg border border-rose-300/20 px-4 text-xs text-rose-100 hover:bg-rose-300/[.06] disabled:opacity-50">Reject draft</button>
        </div>}
        {selectedPlan.status === 'APPROVED' && <div className="flex flex-wrap items-center gap-3 border-t border-white/[.07] pt-4"><p className="flex-1 text-xs text-slate-400">Approval is recorded. Publishing makes this roadmap visible to the student.</p><button type="button" disabled={busy !== '' || !selectedPlan.plan.tasks.length} onClick={() => void planAction('publish')} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"><Send size={14} />{busy === 'publish' ? 'Publishing…' : 'Publish roadmap'}</button></div>}
        {selectedPlan.status === 'PUBLISHED' && <p role="status" className="flex items-center gap-2 border-t border-white/[.07] pt-4 text-xs text-rose-100"><Check size={14} />Published to {selectedStudent?.full_name ?? 'the student'}.</p>}
        {selectedPlan.status === 'REJECTED' && <p className="flex items-center gap-2 border-t border-white/[.07] pt-4 text-xs text-slate-400"><CircleAlert size={14} />Rejected roadmap. Generate a new version after reviewing the recorded evidence.</p>}
      </div>
    </section>}

    {plans.length === 0 && selectedStudent && <section className="surface rounded-xl p-4"><Empty title="No recovery roadmap yet" detail={`Generate a draft for ${selectedStudent.full_name} when there is verified evidence to review.`} /></section>}
    {!loading && monitoring.students.length === 0 && <section className="surface rounded-xl p-5"><Empty title="No assigned students available" detail="Recovery tools are limited to students in courses or sections assigned to your teacher profile." /></section>}

    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="eyebrow">Published tasks</p><h2 className="mt-1 text-base font-semibold text-white">Monitor and assess student work</h2></div><span className="text-[10px] text-slate-500">Only your assigned students are included</span></div>
      {hasSubjectScope && <p className="rounded-lg border border-rose-200/10 bg-rose-200/[.025] px-3 py-2 text-[10px] text-slate-300">Individual Subject Roadmap · {scopedSubject?.name ?? `Subject ${routeSubjectId}`} · Student {selectedStudent?.full_name ?? selectedStudentId}</p>}
      {filteredTasks.length ? filteredTasks.map((task) => <article key={task.id} className="surface rounded-xl p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow">{task.student_name} · {task.student_code}{task.subject ? ` · ${task.subject}` : ''}</p><h3 className="mt-1 text-sm font-semibold text-white">{task.title}</h3></div><span className="rounded-full border border-white/10 px-3 py-1 text-[10px] text-slate-300">{task.status.replaceAll('_', ' ')} · {task.progress_percent}%</span></div>
        {task.deadline && <p className="mt-2 text-[10px] text-slate-500">Deadline {new Date(task.deadline).toLocaleString()}</p>}
        <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">Review assigned lesson and deliverable</summary><div className="mt-2 space-y-2 rounded-md bg-black/15 p-3 text-xs leading-5 text-slate-300"><p><strong className="text-slate-100">Reason:</strong> {task.activity.reason}</p><p><strong className="text-slate-100">Lesson:</strong> {task.activity.lesson}</p><ol className="list-decimal space-y-1 pl-5">{task.activity.steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol><p><strong className="text-slate-100">Exercise:</strong> {task.activity.exercise}</p><p><strong className="text-slate-100">Submission:</strong> {task.activity.submission_format}</p></div></details>
        {task.evidence.length > 0 && <details className="mt-3 text-[10px] text-slate-500"><summary className="cursor-pointer">Review linked academic records</summary><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/20 p-3">{JSON.stringify(task.evidence, null, 2)}</pre></details>}
        {task.status === 'DRAFT' && task.pending_activity && <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/[.035] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-rose-200">Replacement content awaiting approval</p>
          <p className="mt-2 text-xs font-medium text-white">{task.pending_activity.title}</p>
          <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.pending_activity.activity.lesson}</p>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-400">{task.pending_activity.activity.exercise}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" disabled={busy !== ''} onClick={() => editActivity(task)} className="min-h-9 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25 disabled:opacity-50">Edit proposal</button>
            <button type="button" disabled={busy !== ''} onClick={() => void approveActivity(task)} className="min-h-9 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50">{busy === `activity-${task.id}` ? 'Approving…' : 'Approve and release'}</button>
          </div>
        </div>}
        {task.status === 'AVAILABLE' && <button type="button" onClick={() => editActivity(task)} className="mt-3 min-h-9 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:border-rose-300/25">Enrich or replace activity</button>}
        {activityEditors[task.id] && <div className="mt-3 grid gap-3 rounded-lg border border-rose-300/15 bg-black/10 p-4 md:grid-cols-2">
          <label className="text-[10px] text-slate-500 md:col-span-2">Task title<input value={activityEditors[task.id].title} onChange={(event) => updateActivity(task.id, 'title', event.target.value)} className="form-input" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Learning objective<textarea rows={2} value={activityEditors[task.id].learning_objective} onChange={(event) => updateActivity(task.id, 'learning_objective', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Why assigned<textarea rows={2} value={activityEditors[task.id].reason} onChange={(event) => updateActivity(task.id, 'reason', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Lesson<textarea rows={4} value={activityEditors[task.id].lesson} onChange={(event) => updateActivity(task.id, 'lesson', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Activity steps · one per line<textarea rows={4} value={activityEditors[task.id].steps} onChange={(event) => updateActivity(task.id, 'steps', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Practice exercise or deliverable<textarea rows={3} value={activityEditors[task.id].exercise} onChange={(event) => updateActivity(task.id, 'exercise', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500 md:col-span-2">Submission format<textarea rows={2} value={activityEditors[task.id].submission_format} onChange={(event) => updateActivity(task.id, 'submission_format', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500">Instructions<textarea rows={3} value={activityEditors[task.id].instructions} onChange={(event) => updateActivity(task.id, 'instructions', event.target.value)} className="form-input resize-y" /></label>
          <label className="text-[10px] text-slate-500">Completion criteria<textarea rows={3} value={activityEditors[task.id].completion_criteria} onChange={(event) => updateActivity(task.id, 'completion_criteria', event.target.value)} className="form-input resize-y" /></label>
          <div className="flex flex-wrap gap-2 md:col-span-2"><button type="button" disabled={busy !== ''} onClick={() => void saveActivity(task)} className="min-h-10 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50">{busy === `activity-${task.id}` ? 'Saving…' : 'Save replacement for approval'}</button><button type="button" onClick={() => setActivityEditors((current) => { const next = { ...current }; delete next[task.id]; return next; })} className="min-h-10 rounded-lg border border-white/10 px-4 text-xs text-slate-300">Cancel</button></div>
          <p className="text-[10px] leading-5 text-slate-500 md:col-span-2">Saving immediately hides the current activity from the student. The replacement remains unavailable until you explicitly approve it.</p>
        </div>}
        {task.status === 'SUBMITTED' && <div className="mt-3 rounded-lg border border-white/[.07] bg-black/10 p-3"><p className="text-[10px] uppercase tracking-wider text-slate-500">Student response</p><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{task.student_response}</p></div>}
        {task.status === 'SUBMITTED' && <div className="mt-4 grid gap-3 rounded-lg border border-rose-300/15 bg-rose-300/[.025] p-4">
          <label className="text-[10px] text-slate-500">Feedback<textarea rows={3} value={feedback[task.id]?.feedback ?? ''} onChange={(event) => setFeedback((current) => ({ ...current, [task.id]: { feedback: event.target.value, score: current[task.id]?.score ?? '', decision: current[task.id]?.decision ?? 'complete' } }))} className="form-input resize-y" placeholder="Provide evidence-based feedback for this recovery task." /></label>
          <div className="flex flex-wrap items-end gap-3">
            <label className="w-32 text-[10px] text-slate-500">Optional score<input type="number" min="0" max="100" step="0.1" value={feedback[task.id]?.score ?? ''} onChange={(event) => setFeedback((current) => ({ ...current, [task.id]: { feedback: current[task.id]?.feedback ?? '', score: event.target.value, decision: current[task.id]?.decision ?? 'complete' } }))} className="form-input" /></label>
            <label className="min-w-40 flex-1 text-[10px] text-slate-500">Decision<select value={feedback[task.id]?.decision ?? 'complete'} onChange={(event) => setFeedback((current) => ({ ...current, [task.id]: { feedback: current[task.id]?.feedback ?? '', score: current[task.id]?.score ?? '', decision: event.target.value as FeedbackDraft['decision'] } }))} className="form-input"><option value="complete">Complete</option><option value="needs_revision">Request revision</option></select></label>
            <button type="button" disabled={busy === `assess-${task.id}`} onClick={() => void assessTask(task)} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-rose-600 px-4 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50">{busy === `assess-${task.id}` ? 'Saving…' : 'Save feedback'}<ChevronRight size={13} /></button>
          </div>
        </div>}
      </article>) : <section className="surface rounded-xl p-4"><Empty title={hasSubjectScope ? 'No published tasks for this subject' : 'No published recovery tasks to monitor'} detail={hasSubjectScope ? 'No persisted published tasks were found for this student and subject. Other subjects’ tasks are excluded from this view.' : 'Draft and approved plans stay private until explicitly published.'} /></section>}
    </section>
  </div>;
}

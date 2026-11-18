export type StrictRecoveryFocus = {
  evidence_scope: string;
  policy: {
    threshold_score: number;
    threshold_maximum: number;
    strictly_below: boolean;
    active_for_classification: boolean;
  } | null;
  subjects: {
    id: number;
    students: {
      id: number;
      latest_assessment: {
        evidence_status: string;
        maximum_marks_verified: boolean;
        normalized_score: number | null;
        recorded_at: string | null;
      } | null;
    }[];
  }[];
};

export function strictRecoveryEligibleSubjects<T extends StrictRecoveryFocus['subjects'][number]>(
  focus: (Omit<StrictRecoveryFocus, 'subjects'> & { subjects: T[] }) | null,
  studentId: number,
): T[] {
  if (
    !focus
    || focus.evidence_scope !== 'FIRST_INTERNAL'
    || !focus.policy?.active_for_classification
    || focus.policy.threshold_score !== 12
    || focus.policy.threshold_maximum !== 25
    || !focus.policy.strictly_below
  ) {
    return [];
  }

  return focus.subjects.filter((subject) => {
    const assessment = subject.students.find((student) => student.id === studentId)?.latest_assessment;
    return Boolean(
      assessment
      && assessment.evidence_status === 'VERIFIED'
      && assessment.maximum_marks_verified
      && assessment.normalized_score !== null
      && Number.isFinite(assessment.normalized_score)
      && assessment.normalized_score < 12
      && typeof assessment.recorded_at === 'string'
      && Number.isFinite(Date.parse(assessment.recorded_at)),
    );
  });
}

export type DemoRole = 'faculty' | 'student';

export const DEMO_ACCOUNTS: Record<DemoRole, { email: string; password: string }> = {
  faculty: { email: 'teacher@example.com', password: 'teacher123' },
  student: { email: 'student@example.com', password: 'student123' },
};

export type DemoStudent = {
  name: string;
  risk: number;
  reason: string;
};

export const DEMO_STUDENTS: DemoStudent[] = [
  { name: 'Demo Student 013', risk: 86, reason: 'Missed 4 of the last 6 sessions' },
  { name: 'Demo Student 015', risk: 58, reason: 'Internal marks falling for 3 weeks' },
  { name: 'Demo Student 011', risk: 49, reason: 'Two assignments not submitted' },
  { name: 'Demo Student 018', risk: 27, reason: 'Steady attendance and marks' },
  { name: 'Demo Student 012', risk: 12, reason: 'Steady attendance and marks' },
];

export type DemoPlanItem = {
  title: string;
  status: 'In progress' | 'Done' | 'Up next';
  progress: number;
};

export const DEMO_PLAN: DemoPlanItem[] = [
  { title: 'Environmental Studies · Module 3', status: 'In progress', progress: 60 },
  { title: 'Topic review: Biodiversity', status: 'Done', progress: 100 },
  { title: 'Practice quiz', status: 'Up next', progress: 15 },
];

export const DEMO_SUBJECTS = [
  'Computer Networks',
  'Environmental Studies',
  'Research Methodology',
  'Software Engineering',
  'Theory of Computation',
];

export const DEMO_CLASS_RISKS = [
  { name: 'Class A', value: 22, color: 'var(--mint)' },
  { name: 'Class B', value: 48, color: 'var(--blue)' },
  { name: 'Class C', value: 61, color: 'var(--blue)' },
  { name: 'Class D', value: 83, color: 'var(--red)' },
];

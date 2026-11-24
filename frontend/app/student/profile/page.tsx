
'use client';

import { UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ErrorNotice, PageHeading } from '@/components/ui';
import { api } from '@/lib/api';

type Profile = {
  student_id: string;
  full_name: string;
  program?: string | null;
  year?: string | null;
  semester?: string | null;
  section?: string | null;
  department?: string | null;
  branch?: string | null;
  previous_gpa: number | null;
  current_gpa: number | null;
  previous_cgpa?: number | null;
  current_cgpa?: number | null;
  attendance: number;
  assessment: number;
  assignments_completed: number;
};

export default function StudentProfilePage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api<Profile>('/api/v1/students/me')
      .then(setProfile)
      .catch((requestError: Error) => setError(requestError.message));
  }, []);

  if (error) return <ErrorNotice message={error} />;
  if (!profile) return <div className="skeleton h-64 rounded-xl" />;

  const profileDetails: [string, string | number | null | undefined][] = [
    ['Program', profile.program],
    ['Department', profile.department],
    ['Branch', profile.branch],
    ['Year', profile.year],
    ['Semester', profile.semester],
    ['Section', profile.section],
    ['Previous GPA (4.0 scale)', profile.previous_gpa?.toFixed(2) ?? 'Not recorded'],
    ['Current GPA (4.0 scale)', profile.current_gpa?.toFixed(2) ?? 'Not recorded'],
    ['Previous CGPA (10.0 scale)', profile.previous_cgpa?.toFixed(2) ?? 'Not recorded'],
    ['Current CGPA (10.0 scale)', profile.current_cgpa?.toFixed(2) ?? 'Not recorded'],
    ['Attendance', `${profile.attendance.toFixed(1)}%`],
    ['Assessment progress', `${profile.assessment.toFixed(1)}%`],
    ['Assignments completed', profile.assignments_completed],
  ];

  return (
    <div className="fade-up">
      <PageHeading
        eyebrow="Your account"
        title="Profile"
        description="Your academic identity and current progress."
      />

      <section className="surface max-w-3xl rounded-xl p-6">
        <div className="flex items-center gap-4 border-b border-white/8 pb-5">
          <span className="grid size-12 place-items-center rounded-full bg-cyan-200/10 text-cyan-100">
            <UserRound size={22} />
          </span>

          <div>
            <h2 className="text-lg font-semibold text-white">
              {profile.full_name}
            </h2>
            <p className="mt-1 font-mono text-xs text-slate-500">
              {profile.student_id}
            </p>
          </div>
        </div>

        <dl className="mt-5 grid gap-x-8 gap-y-5 sm:grid-cols-2">
          {profileDetails.map(([label, value]) => (
            <div key={label}>
              <dt className="text-[10px] uppercase tracking-[.1em] text-slate-500">
                {label}
              </dt>
              <dd className="mt-1 text-sm text-slate-200">
                {value == null || value === '' ? 'Not provided' : value}
              </dd>
            </div>
          ))}
        </dl>

        <p className="mt-6 border-t border-white/8 pt-4 text-[10px] text-slate-600">
          Only the signed-in student's own profile is shown. GPA and CGPA are
          displayed separately using their respective grading scales.
        </p>
      </section>
    </div>
  );
}

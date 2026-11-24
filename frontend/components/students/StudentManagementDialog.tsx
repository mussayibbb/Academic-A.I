'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { StudentDirectoryOptions, StudentRecord } from '@/lib/students';

type DialogMode = 'create' | 'edit' | 'import';
type FormValues = {
  student_id: string;
  full_name: string;
  email: string;
  program: string;
  year: string;
  semester: string;
  section: string;
  department: string;
  course_id: string;
  previous_gpa: string;
  current_gpa: string;
  attendance: string;
  assessment: string;
  assignments_completed: string;
};
type ImportResult = {
  imported: number;
  rejected: number;
  duplicates: number;
  batch_id: number | null;
  duplicate_import: boolean;
  errors: { row: number; student_id?: string | null; errors: string[] }[];
  source_provenance: { origin?: string; source_description?: string; source_verified?: boolean };
};

const emptyValues: FormValues = {
  student_id: '',
  full_name: '',
  email: '',
  program: '',
  year: '',
  semester: '',
  section: '',
  department: '',
  course_id: '',
  previous_gpa: '0',
  current_gpa: '0',
  attendance: '0',
  assessment: '0',
  assignments_completed: '0',
};

function initialValues(student?: StudentRecord): FormValues {
  if (!student) return emptyValues;
  return {
    student_id: student.student_id,
    full_name: student.full_name,
    email: student.email ?? '',
    program: student.program ?? '',
    year: student.year ?? '',
    semester: student.semester ?? '',
    section: student.section ?? '',
    department: student.department ?? '',
    course_id: student.course_id == null ? '' : String(student.course_id),
    previous_gpa: String(student.previous_gpa),
    current_gpa: String(student.current_gpa),
    attendance: String(student.attendance),
    assessment: String(student.assessment),
    assignments_completed: String(student.assignments_completed),
  };
}

type Props = {
  mode: DialogMode;
  student?: StudentRecord;
  options: StudentDirectoryOptions;
  onClose: () => void;
  onChanged: (message: string) => void;
};

export default function StudentManagementDialog({ mode, student, options, onClose, onChanged }: Props) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const [values, setValues] = useState<FormValues>(() => initialValues(student));
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(busy);
  const [error, setError] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [origin, setOrigin] = useState<'INSTITUTIONAL IMPORT' | 'DEMO DATA'>('INSTITUTIONAL IMPORT');
  const [sourceDescription, setSourceDescription] = useState('');
  const [sourceVerified, setSourceVerified] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  useEffect(() => { busyRef.current = busy; }, [busy]);
  useEffect(() => {
    closeButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyRef.current) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  const setField = (field: keyof FormValues, value: string) => {
    setValues((current) => ({ ...current, [field]: value }));
  };

  const submitRecord = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    const body = {
      ...values,
      email: values.email.trim() || null,
      course_id: values.course_id ? Number(values.course_id) : null,
      previous_gpa: Number(values.previous_gpa),
      current_gpa: Number(values.current_gpa),
      attendance: Number(values.attendance),
      assessment: Number(values.assessment),
      assignments_completed: Number(values.assignments_completed),
    };
    try {
      const path = mode === 'edit' && student
        ? `/api/v1/students/${student.id}`
        : '/api/v1/students';
      const result = await api<{ risk_reassessment?: { message?: string; status?: string } }>(path, {
        method: mode === 'edit' ? 'PUT' : 'POST',
        body: JSON.stringify(body),
      });
      onChanged(result.risk_reassessment?.message ?? 'Student record saved.');
      onClose();
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitImport = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) {
      setError('Choose a student-record CSV file first.');
      return;
    }
    if (origin === 'INSTITUTIONAL IMPORT' && (!sourceVerified || sourceDescription.trim().length < 3)) {
      setError('Enter the institutional source and confirm that it has been verified.');
      return;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('data_origin', origin);
    formData.append('source_description', sourceDescription);
    formData.append('source_verified', String(sourceVerified));
    setBusy(true);
    setError('');
    try {
      const result = await api<ImportResult>('/api/v1/students/import', {
        method: 'POST',
        body: formData,
      });
      setImportResult(result);
      onChanged(`${result.imported} student records imported; ${result.rejected} invalid and ${result.duplicates} duplicate rows.`);
    } catch (requestError) {
      setError((requestError as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const title = mode === 'create' ? 'Add student' : mode === 'edit' ? 'Edit student record' : 'Import student records';

  return <div className="fixed inset-0 z-[80] grid place-items-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !busy) onClose();
  }}>
    <section role="dialog" aria-modal="true" aria-labelledby="student-management-title" className="my-auto max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-white/10 bg-[#111214] p-5 shadow-2xl sm:p-7">
      <div className="flex items-start justify-between gap-4 border-b border-white/[.08] pb-4">
        <div>
          <p className="eyebrow">Student management · auditable records</p>
          <h2 id="student-management-title" className="mt-2 text-lg font-semibold text-white">{title}</h2>
        </div>
        <button ref={closeButtonRef} type="button" onClick={onClose} disabled={busy} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50">Close</button>
      </div>

      {mode === 'import' ? <form onSubmit={submitImport} className="mt-5 space-y-4">
        <p className="text-xs leading-5 text-slate-400">This workflow imports student records only. It does not modify model-training files or the academic-observation import pipeline.</p>
        <label className="block text-xs text-slate-400">Source classification
          <select value={origin} onChange={(event) => setOrigin(event.target.value as typeof origin)} className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-[#0b1320] px-3 text-sm text-slate-200">
            <option value="INSTITUTIONAL IMPORT">Institutional import</option>
            <option value="DEMO DATA">Demo data</option>
          </select>
        </label>
        {origin === 'INSTITUTIONAL IMPORT' && <>
          <label className="block text-xs text-slate-400">Source institution or system
            <input value={sourceDescription} onChange={(event) => setSourceDescription(event.target.value)} required minLength={3} maxLength={200} placeholder="Example: Registrar export, 2026 semester 1" className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-[#0b1320] px-3 text-sm text-slate-200 outline-none focus:border-cyan-200/50" />
          </label>
          <label className="flex items-start gap-2 text-xs leading-5 text-slate-300">
            <input type="checkbox" checked={sourceVerified} onChange={(event) => setSourceVerified(event.target.checked)} required className="mt-1 accent-cyan-300" />
            I verified this file came from the institution or system named above.
          </label>
        </>}
        <label className="block text-xs text-slate-400">CSV file
          <input type="file" accept=".csv,text/csv" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setImportResult(null); }} required className="mt-2 block w-full rounded-lg border border-white/10 bg-[#0b1320] p-2 text-xs text-slate-300 file:mr-3 file:rounded-md file:border-0 file:bg-white/10 file:px-3 file:py-2 file:text-xs file:text-slate-200" />
        </label>
        <p className="text-[10px] leading-5 text-slate-500">Required columns: <code>student_id</code>, <code>full_name</code>. Optional: email, program, year, semester, section, department, course_code, GPA, attendance, assessment, and assignments_completed.</p>
        {error && <p role="alert" className="rounded-lg border border-rose-300/20 bg-rose-300/5 p-3 text-xs text-rose-100">{error}</p>}
        {importResult && <div role="status" className="rounded-lg border border-cyan-200/15 bg-cyan-200/[.04] p-3 text-xs text-slate-200">
          <p>{importResult.duplicate_import ? 'This exact file was already processed.' : 'Import complete.'} {importResult.imported} added · {importResult.duplicates} duplicates · {importResult.rejected} invalid.</p>
          <p className="mt-1 text-[10px] text-slate-400">Recorded source: {importResult.source_provenance.origin ?? origin} · {(importResult.source_provenance.source_description ?? sourceDescription) || 'Demo data import'}</p>
          {importResult.errors.slice(0, 5).map((item) => <p key={`${item.row}-${item.student_id ?? ''}`} className="mt-1 text-[10px] text-amber-100">Row {item.row}{item.student_id ? ` (${item.student_id})` : ''}: {item.errors.join(' ')}</p>)}
          {importResult.errors.length > 5 && <p className="mt-1 text-[10px] text-slate-500">Showing the first five row issues.</p>}
        </div>}
        <div className="flex justify-end gap-2 border-t border-white/[.08] pt-4">
          <button type="button" onClick={onClose} disabled={busy} className="rounded-lg border border-white/10 px-4 py-2.5 text-xs text-slate-300">Close</button>
          <button type="submit" disabled={busy} className="rounded-lg bg-cyan-500 px-4 py-2.5 text-xs font-semibold text-slate-950 disabled:opacity-50">{busy ? 'Importing…' : 'Import CSV'}</button>
        </div>
      </form> : <form onSubmit={submitRecord} className="mt-5">
        <p className="mb-4 text-xs leading-5 text-slate-400">{mode === 'edit'
          ? <>Current provenance: <span className="text-slate-200">{student?.data_origin}</span>. Editing a record does not change its source classification.</>
          : 'This record will be marked MANUALLY ADDED. Institution and demo provenance are assigned only through the verified CSV workflow.'}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-slate-400">Student ID<input value={values.student_id} onChange={(event) => setField('student_id', event.target.value)} required maxLength={40} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Full name<input value={values.full_name} onChange={(event) => setField('full_name', event.target.value)} required maxLength={160} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Email<input type="email" value={values.email} onChange={(event) => setField('email', event.target.value)} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Department<input value={values.department} onChange={(event) => setField('department', event.target.value)} maxLength={160} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Program<input value={values.program} onChange={(event) => setField('program', event.target.value)} maxLength={160} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Year<input value={values.year} onChange={(event) => setField('year', event.target.value)} maxLength={30} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Semester<input value={values.semester} onChange={(event) => setField('semester', event.target.value)} maxLength={40} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Section<input value={values.section} onChange={(event) => setField('section', event.target.value)} maxLength={40} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Course
            <select value={values.course_id} onChange={(event) => setField('course_id', event.target.value)} className="form-input mt-2">
              <option value="">No course assigned</option>
              {options.courses.map((course) => <option key={course.id} value={course.id}>{course.code} · {course.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">Previous GPA<input type="number" min="0" max="4" step="0.01" value={values.previous_gpa} onChange={(event) => setField('previous_gpa', event.target.value)} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Current GPA<input type="number" min="0" max="4" step="0.01" value={values.current_gpa} onChange={(event) => setField('current_gpa', event.target.value)} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Attendance (%)<input type="number" min="0" max="100" step="0.1" value={values.attendance} onChange={(event) => setField('attendance', event.target.value)} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400">Assessment (%)<input type="number" min="0" max="100" step="0.1" value={values.assessment} onChange={(event) => setField('assessment', event.target.value)} className="form-input mt-2" /></label>
          <label className="text-xs text-slate-400 sm:col-span-2">Assignments completed<input type="number" min="0" step="1" value={values.assignments_completed} onChange={(event) => setField('assignments_completed', event.target.value)} className="form-input mt-2" /></label>
        </div>
        {error && <p role="alert" className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/5 p-3 text-xs text-rose-100">{error}</p>}
        <div className="mt-5 flex justify-end gap-2 border-t border-white/[.08] pt-4">
          <button type="button" onClick={onClose} disabled={busy} className="rounded-lg border border-white/10 px-4 py-2.5 text-xs text-slate-300">Cancel</button>
          <button type="submit" disabled={busy} className="rounded-lg bg-rose-600 px-4 py-2.5 text-xs font-semibold text-white disabled:opacity-50">{busy ? 'Saving…' : mode === 'edit' ? 'Save changes' : 'Add student'}</button>
        </div>
      </form>}
    </section>
  </div>;
}

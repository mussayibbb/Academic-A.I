export type StudentRecord = {
  id: number;
  student_id: string;
  full_name: string;
  email?: string | null;
  program?: string | null;
  year?: string | null;
  semester?: string | null;
  section?: string | null;
  department?: string | null;
  course_id?: number | null;
  data_origin: string;
  risk_level: string;
  risk_probability: number;
  risk_status?: string;
  risk_status_message?: string | null;
  attendance: number;
  assessment: number;
  previous_gpa: number | null;
  current_gpa: number | null;
  previous_cgpa?: number | null;
  current_cgpa?: number | null;
  assignments_completed: number;
  momentum_trend?: string;
  branch?: string | null;
};

export type StudentDirectoryOptions = {
  departments: string[];
  programs: string[];
  semesters: string[];
  sections: string[];
  courses: { id: number; code: string; name: string }[];
};

export type StudentStatistics = {
  total: number;
  high_risk: number | null;
  medium_risk: number | null;
  low_risk: number | null;
  average_attendance: number | null;
  average_gpa: number | null;
  attention: number | null;
  recently_added: number;
  by_origin: Record<string, number>;
  prediction_status: 'available' | 'unvalidated-demo-model' | 'unavailable' | string;
  prediction_message?: string | null;
};

'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type RiskHistoryPoint = {
  date: string;
  HIGH: number;
  MEDIUM: number;
  LOW: number;
};

export default function TeacherRiskHistoryChart({ data }: { data: RiskHistoryPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data}>
        <CartesianGrid stroke="rgba(255,255,255,.08)" vertical={false} />
        <XAxis dataKey="date" tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ background: '#25282d', border: '1px solid #454a52', borderRadius: 8, color: '#f7f8fa', fontSize: 11 }} />
        <Bar dataKey="HIGH" stackId="risk" fill="#e11d32" radius={[3, 3, 0, 0]} />
        <Bar dataKey="MEDIUM" stackId="risk" fill="#747b85" />
        <Bar dataKey="LOW" stackId="risk" fill="#e6e8eb" />
      </BarChart>
    </ResponsiveContainer>
  );
}

'use client';

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type ProgressPoint = {
  date: string;
  attendance: number;
  assessment?: number;
};

export default function StudentProgressChart({ data }: { data: ProgressPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ left: -20, right: 8, top: 8, bottom: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,.08)" vertical={false} />
        <XAxis dataKey="date" tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis domain={[0, 100]} tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={{ background: '#25282d', border: '1px solid #454a52', borderRadius: 8, color: '#f7f8fa', fontSize: 11 }} />
        <Line type="monotone" dataKey="attendance" stroke="#e11d32" strokeWidth={2} dot={{ r: 2, fill: '#e11d32' }} />
        <Line type="monotone" dataKey="assessment" stroke="#e6e8eb" strokeWidth={2} dot={{ r: 2, fill: '#e6e8eb' }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

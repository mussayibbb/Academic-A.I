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

type FeatureImportance = {
  name: string;
  importance: number;
};

export default function TeacherFeatureImportanceChart({ data }: { data: FeatureImportance[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ left: 12, right: 20, top: 4, bottom: 4 }}>
        <CartesianGrid stroke="rgba(255,255,255,.08)" horizontal={false} />
        <XAxis type="number" tick={{ fill: '#9299a2', fontSize: 10 }} axisLine={false} />
        <YAxis type="category" dataKey="name" width={132} tickFormatter={(value: string) => value.replaceAll('_', ' ')} tick={{ fill: '#d1d4d8', fontSize: 10 }} axisLine={false} />
        <Tooltip contentStyle={{ background: '#25282d', border: '1px solid #454a52', borderRadius: 8, color: '#f7f8fa', fontSize: 11 }} />
        <Bar dataKey="importance" fill="#e11d32" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

import type { Metadata } from 'next';
import LandingPage from '@/components/landing/LandingPage';

export const metadata: Metadata = {
  title: 'Academic AI for Impact',
  description: 'Spot who needs help earlier, understand the signals, and act in time.',
};

export default function HomePage() {
  return <LandingPage />;
}

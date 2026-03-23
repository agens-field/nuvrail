import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Nuvrail — The approval layer between AI agents and your inbox.',
  description:
    'Nuvrail sits between your AI agent and your inbox. Every proposed action is staged for human review, logged immutably, and held until you approve the diff.',
  openGraph: {
    title: 'Nuvrail — The approval layer between AI agents and your inbox.',
    description:
      'The pull request model for AI email access. Every proposed action is staged, diffed, and held for your approval.',
    type: 'website',
    url: 'https://nuvrail.com',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Nuvrail — The approval layer between AI agents and your inbox.',
    description:
      'The pull request model for AI email access. Every proposed action is staged, diffed, and held for your approval.',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

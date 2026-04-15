'use client';

import { useState } from 'react';

export default function Hero() {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;

    setStatus('loading');
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (res.ok) {
        setStatus('success');
      } else {
        setStatus('error');
      }
    } catch {
      setStatus('error');
    }
  }

  return (
    <section className="border-b border-[#334155]">
      <div className="max-w-4xl mx-auto px-6 py-24 md:py-36">
        {/* Badge */}
        <div className="mb-8">
          <span className="font-mono text-xs text-[#818cf8] bg-[#818cf811] border border-[#818cf833] px-3 py-1 rounded-sm tracking-wider uppercase">
            Early access
          </span>
        </div>

        {/* Problem hook — leads with the fear, then names the solution */}
        <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold leading-tight tracking-tight text-white mb-4">
          Your AI agent is about to send that email. Are you sure?
        </h1>

        <p className="text-xl md:text-2xl font-semibold text-[#818cf8] mb-4">
          The approval layer between AI agents and your inbox.
        </p>

        <p className="text-base md:text-lg text-gray-500 font-medium mb-6">
          Email has no git. Until now.
        </p>

        <p className="text-lg md:text-xl text-gray-400 leading-relaxed max-w-2xl mb-10">
          Nuvrail sits between your AI agent and your inbox. Every proposed action is staged for human review, logged immutably, and held until you approve the diff.
        </p>

        {/* CTA form */}
        {status === 'success' ? (
          <div className="inline-flex items-center gap-3 bg-[#818cf811] border border-[#818cf844] rounded px-5 py-4">
            <span className="text-[#818cf8] font-mono text-sm">✓</span>
            <span className="text-[#818cf8] font-medium">You&apos;re on the list. We&apos;ll be in touch.</span>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3 max-w-xl">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              className="flex-1 bg-[#1e293b] border border-[#334155] text-white placeholder-gray-600 rounded px-4 py-3 text-sm focus:outline-none focus:border-[#818cf8] focus:ring-1 focus:ring-[#818cf8] transition-colors"
              disabled={status === 'loading'}
            />
            <button
              type="submit"
              disabled={status === 'loading' || !email}
              className="bg-[#818cf8] text-white font-semibold px-6 py-3 rounded text-sm hover:bg-[#4338ca] disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
            >
              {status === 'loading' ? 'Submitting…' : 'Get early access'}
            </button>
          </form>
        )}

        {status === 'error' && (
          <p className="mt-3 text-sm text-red-400">
            Something went wrong — email us at{' '}
            <a href="mailto:hello@nuvrail.com" className="underline">
              hello@nuvrail.com
            </a>
          </p>
        )}
      </div>
    </section>
  );
}

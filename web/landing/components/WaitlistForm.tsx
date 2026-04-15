'use client';

import { useState } from 'react';

type FormState = {
  email: string;
  companyName: string;
  deployingAgents: string;
};

type Status = 'idle' | 'loading' | 'success' | 'error';

export default function WaitlistForm() {
  const [form, setForm] = useState<FormState>({
    email: '',
    companyName: '',
    deployingAgents: '',
  });
  const [status, setStatus] = useState<Status>('idle');

  function handleChange(
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.email) return;

    setStatus('loading');
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
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
    <section id="waitlist" className="border-b border-[#334155]">
      <div className="max-w-4xl mx-auto px-6 py-20">
        <div className="max-w-xl">
          <p className="font-mono text-xs text-gray-500 uppercase tracking-wider mb-4">
            Get early access
          </p>
          <h2 className="text-2xl md:text-3xl font-bold text-white mb-2">
            Be first when we open the doors.
          </h2>
          <p className="text-gray-400 mb-2">
            We&apos;re building with a small group of early-access teams. If you&apos;re deploying AI agents on email infrastructure, we want to hear from you.
          </p>
          <p className="text-sm text-gray-500 mb-8">
            Early access + architecture notes from the team.
          </p>

          {status === 'success' ? (
            <div className="bg-[#818cf811] border border-[#818cf844] rounded p-6">
              <p className="text-[#818cf8] font-medium">
                You&apos;re on the list. We&apos;ll be in touch.
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <input
                  type="email"
                  name="email"
                  required
                  value={form.email}
                  onChange={handleChange}
                  placeholder="you@company.com"
                  disabled={status === 'loading'}
                  className="w-full bg-[#1e293b] border border-[#334155] text-white placeholder-gray-600 rounded px-4 py-3 text-sm focus:outline-none focus:border-[#818cf8] focus:ring-1 focus:ring-[#818cf8] transition-colors"
                />
              </div>

              <div>
                <input
                  type="text"
                  name="companyName"
                  value={form.companyName}
                  onChange={handleChange}
                  placeholder="Company (optional)"
                  disabled={status === 'loading'}
                  className="w-full bg-[#1e293b] border border-[#334155] text-white placeholder-gray-600 rounded px-4 py-3 text-sm focus:outline-none focus:border-[#818cf8] focus:ring-1 focus:ring-[#818cf8] transition-colors"
                />
              </div>

              <div>
                <select
                  name="deployingAgents"
                  value={form.deployingAgents}
                  onChange={handleChange}
                  disabled={status === 'loading'}
                  className="w-full bg-[#1e293b] border border-[#334155] text-gray-400 rounded px-4 py-3 text-sm focus:outline-none focus:border-[#818cf8] focus:ring-1 focus:ring-[#818cf8] transition-colors appearance-none"
                >
                  <option value="">Deploying AI agents that touch email today?</option>
                  <option value="yes-actively">Yes, actively</option>
                  <option value="planning-to">Planning to</option>
                  <option value="not-yet">Not yet</option>
                </select>
              </div>

              <button
                type="submit"
                disabled={status === 'loading' || !form.email}
                className="w-full bg-[#818cf8] text-white font-semibold px-6 py-3 rounded text-sm hover:bg-[#4338ca] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {status === 'loading' ? 'Submitting…' : 'Join the waitlist'}
              </button>

              <p className="text-xs text-gray-600">
                No spam. We&apos;ll reach out when we&apos;re ready for early access teams.
              </p>

              {status === 'error' && (
                <p className="text-sm text-red-400">
                  Something went wrong — email us at{' '}
                  <a href="mailto:hello@nuvrail.com" className="underline">
                    hello@nuvrail.com
                  </a>
                </p>
              )}
            </form>
          )}
        </div>
      </div>
    </section>
  );
}

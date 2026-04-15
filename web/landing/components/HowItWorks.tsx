const steps = [
  {
    number: '01',
    title: 'Connect',
    description:
      'Your AI agent connects to Nuvrail using standard IMAP/SMTP credentials. No code changes required on the agent side.',
  },
  {
    number: '02',
    title: 'Stage',
    description:
      'Every proposed action is staged — you see exactly what the agent wants to do, presented as a readable diff before anything happens.',
  },
  {
    number: '03',
    title: 'Decide',
    description:
      'You approve or reject. Approved actions execute against your real inbox. Rejected actions are cleanly reversed. The agent only proceeds when you say so.',
  },
];

export default function HowItWorks() {
  return (
    <section className="border-b border-[#334155]">
      <div className="max-w-4xl mx-auto px-6 py-20">
        <p className="font-mono text-xs text-gray-500 uppercase tracking-wider mb-12">
          How it works
        </p>

        <div className="grid md:grid-cols-3 gap-8 md:gap-12">
          {steps.map((step) => (
            <div key={step.number} className="relative">
              <div className="font-mono text-3xl font-bold text-[#818cf8] mb-4">
                {step.number}
              </div>
              <h3 className="text-white font-semibold text-lg mb-3">{step.title}</h3>
              <p className="text-gray-400 text-sm leading-relaxed">{step.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

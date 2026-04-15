export default function Problem() {
  return (
    <section className="border-b border-[#334155]">
      <div className="max-w-4xl mx-auto px-6 py-20">
        <div className="max-w-2xl">
          <p className="font-mono text-xs text-gray-500 uppercase tracking-wider mb-6">
            The problem
          </p>
          <p className="text-xl md:text-2xl text-gray-200 leading-relaxed">
            AI agents that touch email today have unrestricted access — no review mechanism, no audit trail, no undo. When something goes wrong, there&apos;s no record of what happened and no way to reverse it.
          </p>
          <p className="text-xl md:text-2xl text-gray-400 leading-relaxed mt-4">
            That&apos;s not a risk you can accept.
          </p>
        </div>

        {/* Approval card — shows exactly what human review looks like */}
        <div className="mt-14 flex justify-start">
          <div className="w-full max-w-lg">
            <img
              src="/app-card.png"
              alt="Nuvrail approval card: AI agent requested to send an email, awaiting human approve or reject"
              className="w-full rounded-lg shadow-2xl shadow-black/50"
            />
            <p className="mt-3 text-xs text-slate-500">
              Every proposed action staged for review before it touches your inbox.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

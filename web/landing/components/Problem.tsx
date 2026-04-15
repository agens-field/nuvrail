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

        {/* App screenshot — shows the problem solved in practice */}
        <div className="mt-14">
          <div className="rounded-lg overflow-hidden border border-[#334155] shadow-2xl shadow-black/50">
            <div className="bg-[#1e293b] border-b border-[#334155] px-4 py-2.5 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#334155]" />
              <span className="w-3 h-3 rounded-full bg-[#334155]" />
              <span className="w-3 h-3 rounded-full bg-[#334155]" />
              <span className="font-mono text-xs text-slate-500 ml-2">test.nuvrail.com</span>
            </div>
            <img
              src="/app-screenshot.png"
              alt="Nuvrail approval interface showing a staged email operation awaiting human review"
              className="w-full block"
            />
          </div>
          <p className="mt-3 text-xs text-slate-500 text-center">
            Every proposed action staged for review before it touches your inbox.
          </p>
        </div>
      </div>
    </section>
  );
}

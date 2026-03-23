export default function Solution() {
  return (
    <section className="border-b border-[#2a2a2a]">
      <div className="max-w-4xl mx-auto px-6 py-20">
        <div className="max-w-2xl">
          <p className="font-mono text-xs text-gray-500 uppercase tracking-wider mb-6">
            The solution
          </p>

          <p className="text-xl md:text-2xl text-white leading-relaxed mb-6">
            Nuvrail is the{' '}
            <span className="font-mono text-[#2dd4bf]">pull request model</span> for email.
          </p>

          <div className="space-y-4 text-lg text-gray-300 leading-relaxed">
            <p>
              Every proposed action — mark as read, move to folder, send a reply — is staged for your review before anything touches your real inbox.
            </p>
            <p>
              You see a readable diff. You approve or reject. The agent proceeds only when you say so.
            </p>
          </div>

          {/* PR analogy code block */}
          <div className="mt-10 bg-[#1a1a1a] border border-[#2a2a2a] rounded p-5 font-mono text-sm">
            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-[#2a2a2a]">
              <span className="w-3 h-3 rounded-full bg-[#2a2a2a]" />
              <span className="w-3 h-3 rounded-full bg-[#2a2a2a]" />
              <span className="w-3 h-3 rounded-full bg-[#2a2a2a]" />
              <span className="text-gray-600 text-xs ml-2">nuvrail / staged-actions</span>
            </div>
            <div className="space-y-1 text-xs md:text-sm">
              <div className="text-gray-500">proposed by: gpt-4o-agent@acme.com</div>
              <div className="text-gray-500 mb-3">status: awaiting approval</div>
              <div className="text-red-400">- DELETE thread: &quot;Q3 forecast — FINAL v7&quot;</div>
              <div className="text-green-400">+ MOVE to: /Archive/2024/Finance</div>
              <div className="mt-3 flex gap-3">
                <span className="text-[#2dd4bf]">[approve]</span>
                <span className="text-red-400">[reject]</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

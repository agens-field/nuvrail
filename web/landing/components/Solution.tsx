export default function Solution() {
  return (
    <section className="border-b border-[#334155]">
      <div className="max-w-4xl mx-auto px-6 py-20">
        <div className="max-w-2xl">
          <p className="font-mono text-xs text-gray-500 uppercase tracking-wider mb-6">
            The solution
          </p>

          <p className="text-xl md:text-2xl text-white leading-relaxed mb-6">
            Nuvrail is the{' '}
            <span className="font-mono text-[#818cf8]">pull request model</span> for email.
          </p>

          <div className="space-y-4 text-lg text-gray-300 leading-relaxed">
            <p>
              Every proposed action — mark as read, move to folder, send a reply — is staged for your review before anything touches your real inbox.
            </p>
            <p>
              You see a readable diff. You approve or reject. The agent proceeds only when you say so.
            </p>
          </div>

          {/* Approval card — the real UI, not a mock */}
          <div className="mt-10">
            <img
              src="/app-card.png"
              alt="Nuvrail approval card: AI agent requested to send an email, awaiting human approve or reject"
              className="w-full rounded-lg shadow-2xl shadow-black/50"
            />
          </div>
        </div>
      </div>
    </section>
  );
}

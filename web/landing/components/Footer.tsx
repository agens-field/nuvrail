export default function Footer() {
  return (
    <footer className="py-10 px-6">
      <div className="max-w-4xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-gray-600">
        <p className="font-mono">© 2026 Nuvrail</p>
        <div className="flex items-center gap-6">
          <a
            href="https://github.com/AnimalHorde/nuvrail"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-gray-400 transition-colors"
          >
            GitHub
          </a>
          <a
            href="mailto:hello@nuvrail.com"
            className="hover:text-gray-400 transition-colors"
          >
            hello@nuvrail.com
          </a>
        </div>
      </div>
    </footer>
  );
}

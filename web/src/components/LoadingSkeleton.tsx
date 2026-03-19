export default function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="bg-slate-800 rounded-lg border border-slate-700 p-4 animate-pulse"
        >
          <div className="flex items-center gap-2 mb-3">
            <div className="h-5 w-14 bg-slate-700 rounded" />
            <div className="h-5 flex-1 bg-slate-700 rounded" />
          </div>
          <div className="h-4 w-3/4 bg-slate-700 rounded mb-2" />
          <div className="h-4 w-1/2 bg-slate-700 rounded mb-4" />
          <div className="flex justify-end gap-2">
            <div className="h-8 w-20 bg-slate-700 rounded-md" />
            <div className="h-8 w-20 bg-slate-700 rounded-md" />
          </div>
        </div>
      ))}
    </div>
  )
}

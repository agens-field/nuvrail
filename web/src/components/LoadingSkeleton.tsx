export default function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="bg-surface rounded-lg border border-edge p-4 animate-pulse"
        >
          <div className="flex items-center gap-2 mb-3">
            <div className="h-5 w-14 bg-surface-hi rounded" />
            <div className="h-5 flex-1 bg-surface-hi rounded" />
          </div>
          <div className="h-4 w-3/4 bg-surface-hi rounded mb-2" />
          <div className="h-4 w-1/2 bg-surface-hi rounded mb-4" />
          <div className="flex justify-end gap-2">
            <div className="h-8 w-20 bg-surface-hi rounded-md" />
            <div className="h-8 w-20 bg-surface-hi rounded-md" />
          </div>
        </div>
      ))}
    </div>
  )
}

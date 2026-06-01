import { useQuery } from '@tanstack/react-query'
import { fetchFeatures, getToken, type FeaturesResponse } from '../api/client'

/**
 * Fetch the server's feature/entitlement flags (GET /api/v1/features).
 *
 * Only runs when authenticated (so it never triggers the 401→login redirect on
 * the public pages). Returns `undefined` while unauthenticated or loading.
 *
 * Used to show/hide gated UI — e.g. the auto-approval Rules section, which is
 * an enterprise feature and is reported as unavailable in open-core builds.
 */
export function useFeatures(): FeaturesResponse | undefined {
  const { data } = useQuery({
    queryKey: ['features'],
    queryFn: fetchFeatures,
    enabled: !!getToken(),
    staleTime: 5 * 60 * 1000,
  })
  return data
}

/** Convenience: is a named feature enabled for the current user? */
export function useFeatureEnabled(name: string): boolean {
  const features = useFeatures()
  return Boolean(features?.features?.[name])
}

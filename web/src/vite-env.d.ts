/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_PROXY_HOST?: string
  readonly VITE_PLAUSIBLE_DOMAIN?: string
  readonly VITE_PRIVACY_POLICY_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

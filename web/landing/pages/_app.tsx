import type { AppProps } from 'next/app';
import CookieConsentBanner from '../components/CookieConsentBanner';
import '../styles/globals.css';

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Component {...pageProps} />
      {/* GDPR cookie consent — rendered outside page tree so it persists across routes */}
      <CookieConsentBanner />
    </>
  );
}

import Hero from '@/components/Hero';
import Problem from '@/components/Problem';
import Solution from '@/components/Solution';
import HowItWorks from '@/components/HowItWorks';
import TrustSignals from '@/components/TrustSignals';
import WaitlistForm from '@/components/WaitlistForm';
import Footer from '@/components/Footer';

export default function Home() {
  return (
    <main className="min-h-screen bg-[#0f0f0f] text-gray-100">
      <Hero />
      <Problem />
      <Solution />
      <HowItWorks />
      <TrustSignals />
      <WaitlistForm />
      <Footer />
    </main>
  );
}

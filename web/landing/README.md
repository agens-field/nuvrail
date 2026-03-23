# Nuvrail Landing Page

Stage 1 waitlist capture page. Next.js 14 + TypeScript + Tailwind CSS.

## Local Development

```bash
cd web/landing
npm install
cp .env.example .env.local
# Edit .env.local and set LOOPS_API_KEY=<your_loops_api_key>
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Build

```bash
npm run build
```

## Environment Variables

| Variable       | Required | Description                                      |
|---------------|----------|--------------------------------------------------|
| `LOOPS_API_KEY` | Yes    | API key from [Loops.so](https://loops.so) dashboard |

Copy `.env.example` to `.env.local` and fill in the values. Never commit `.env.local`.

## Deployment

### Vercel (recommended)

1. Import the repo in Vercel
2. Set the **Root Directory** to `web/landing`
3. Add `LOOPS_API_KEY` as an environment variable
4. Deploy

### Static Export

For a fully static build (no API routes — Loops integration won't work server-side):

Uncomment `output: 'export'` in `next.config.js`, then run `npm run build`. The static files will be in `out/`.

> Note: The waitlist API route (`/api/waitlist`) requires a Node.js server. Static export disables it. For static hosts, replace the API route with a direct client-side POST to Loops.so (requires exposing the API key — use an environment variable injected at build time and restrict CORS on the Loops side).

## Architecture

```
web/landing/
├── app/
│   ├── layout.tsx          # Root layout + metadata
│   ├── page.tsx            # Page composition
│   ├── globals.css         # Global styles + Tailwind directives
│   └── api/
│       └── waitlist/
│           └── route.ts    # Server-side Loops.so proxy (keeps API key out of browser)
└── components/
    ├── Hero.tsx            # Headline, sub-headline, quick email CTA
    ├── Problem.tsx         # Problem statement (2–3 sentences)
    ├── Solution.tsx        # PR analogy + code-block visualization
    ├── HowItWorks.tsx      # 3-step explainer
    ├── TrustSignals.tsx    # Open source / audit log / no deletes
    ├── WaitlistForm.tsx    # Full segmentation form
    └── Footer.tsx          # Copyright + links
```

## Loops.so Integration

The `/api/waitlist` route POSTs to `https://app.loops.so/api/v1/contacts/create` with:

- `email` — required
- `userGroup` — always `"waitlist"`
- `source` — always `"landing-page"`
- `companyName` — optional
- `deployingAgents` — optional, one of: `yes-actively`, `evaluating`, `not-yet`

Get your API key from [Loops.so → Settings → API](https://app.loops.so/settings?page=api).

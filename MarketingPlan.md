# Nuvrail — Marketing & Communications Plan

**Status:** Draft v3  
**Date:** March 16, 2026  
**Author:** Stella (CMO)  
**Inputs:** MarketingEthos.md (v2), SPEC.md  
**Board decisions incorporated:** Open source core (v2), SMTP at launch (v2), Nuvrail as unified company/product name (v2), pricing model (v2), dual founding story / founding story PR priority / developer press sequencing / customer success / agent-agnostic amplification (v3)

---

## The One Thing (Non-Negotiable)

Every campaign, every blog post, every demo, every cold email traces back to this:

> **Nuvrail is the pull request model for AI email access. Nothing reaches your inbox without a human approving the diff.**

If a piece of content doesn't reinforce this, we don't publish it.

---

## 1. Positioning

### The insight

Every other layer of AI tooling has a review mechanism. Code has git. Documents have track changes. Deployments have approval gates. Email has nothing. Giving an AI agent raw IMAP credentials today means unrestricted access, no audit trail, no undo.

Nuvrail is the missing layer.

### What we are

The approval gateway between AI agents and real inboxes. Provider-agnostic. Agent-agnostic. Every proposed action staged, reviewed, logged. The core is open source — anyone can run it, audit it, and contribute to it. The managed service is how most teams will actually use it.

### What we are not

- A premium email client
- A workflow automation tool
- An AI assistant
- A closed ecosystem

### Primary audience

**Enterprise buyer responsible for AI deployment** — CTO, CISO, VP Engineering. They've been asked to greenlight AI email access. They want to say yes. They need a reason to feel safe doing so. We are that reason.

### Secondary audience

**AI developers building agents that touch email.** They want their product to be enterprise-deployable. Nuvrail makes that possible without them building an approval layer from scratch.

### Competitive landscape

| Competitor | Gap they leave open |
|---|---|
| Nylas | IMAP abstraction with no approval layer. $45M raised; trust still unsolved. |
| Microsoft Copilot / Google Workspace AI | Closed ecosystems. Approval on their terms only. |
| Zapier / Make / n8n | DIY Zap + Gmail + Slack bot. Fragile, unaudited, not built for AI agents. |

The DIY automation category is our most important near-term competitive target. These are teams who already feel the problem and have tried to solve it themselves. We are the better answer.

---

## 2. Messaging Framework

### Headline (website, press, one-pager)
> The pull request model for AI email access.

### Sub-headline
> Nuvrail sits between your AI agent and your inbox. Every proposed action is staged for human review, logged immutably, and held until you approve the diff.

### Problem statement (one sentence)
> AI agents that touch email today have unrestricted access — no review mechanism, no audit trail, no undo.

### Solution statement (one sentence)
> Nuvrail is the approval gateway that changes that: every write is a diff, every approval is a commit, every action is logged forever.

### Agent-agnostic message (use early and often)
> Nuvrail works with any AI agent and any email provider. We have no incentive to lock you in — and that's the point.

This is a meaningful trust signal that distinguishes us from every closed-ecosystem competitor. It should appear on the pricing page, in the integration guide, and in enterprise sales conversations. "We don't care what AI you're using" is the plain-language version.

### Trust proof points (in priority order)
1. **Open source core** — the proxy, staging logic, and audit schema are public; anyone can audit how it works
2. **No permanent deletes** — the gateway blocks EXPUNGE; nothing is gone until you say so
3. **Immutable audit log** — every proposed, approved, and rejected action is recorded
4. **IMAP and SMTP** — covers the full email surface: reads, writes, and sends
5. **Provider-agnostic** — works with any IMAP/SMTP server; not locked to Gmail or any ecosystem
6. **Agent-agnostic** — the AI sees standard protocols; Nuvrail requires no agent modifications
7. **Human-readable diffs** — approvals show plain language, not raw commands

### Tagline options (for testing)
- *The approval layer between AI agents and your inbox.*
- *AI agents for email. With a human in the loop.*
- *Nothing reaches your inbox without your approval.*

Recommend A/B testing the first two on the landing page at launch.

---

## 3. Audiences & Personas

### Persona 1 — "The Cautious CTO" (primary)
**Name:** Dana  
**Role:** CTO at a 150-person B2B SaaS company  
**Situation:** The CEO wants to deploy an AI assistant that handles email triage and follow-ups. Dana's job is to make it safe enough to say yes to.  
**Fear:** "If I give the agent raw IMAP credentials and something goes wrong — a client email deleted, a draft sent prematurely — that's on me."  
**What they need from us:** Proof that Nuvrail closes the loop. Audit trail, approval gate, no permanent deletes. Architecture they can show to their CISO.  
**Where they live:** Hacker News, engineering blogs, LinkedIn, word of mouth from peers.

### Persona 2 — "The Agent Builder" (secondary)
**Name:** Priya  
**Role:** Founding engineer at an AI startup building an email automation agent  
**Situation:** First enterprise prospect asked "what happens if the agent makes a mistake?" Priya doesn't have an answer.  
**Fear:** Losing the deal because they can't credibly answer the trust question.  
**What they need from us:** A drop-in approval layer they can point to. Fast integration, clear docs, no need to build it themselves. Cloud Starter lets them start free — no procurement conversation, no credit card.  
**Where they live:** GitHub, Discord developer communities, AI/agent-focused newsletters (The Rundown, TLDR AI), X/Twitter.

### Persona 3 — "The DIY Escapee" (near-term conversion target)
**Name:** Rodrigo  
**Role:** Senior engineer or team lead who built a Zap + Gmail + Slack approval bot  
**Situation:** It works until it doesn't. No audit log. Breaks on edge cases. They know it's not the right answer.  
**Fear:** Something will go wrong and they won't be able to explain what happened.  
**What they need from us:** A real solution that handles what their bot can't. A migration path that doesn't require ripping out existing workflows.  
**Where they live:** Slack communities, Reddit (r/devops, r/MachineLearning), Hacker News Show HN threads.

---

## 4. Launch Strategy

### Phase 0 — Pre-launch (Now → Milestone 2 complete)
Goal: Build the audience before we have a product to sell.

**Actions:**
- Publish the founding insight as a long-form post: *"Email is the most dangerous thing you can give an AI agent — and nobody's talking about it."* Post on the Nuvrail blog and submit to Hacker News.
- Open a waitlist landing page. Single CTA: "Get early access." Capture email + company size + "are you deploying AI agents today?"
- Jack and KC begin posting on X/Twitter and LinkedIn: short, sharp takes on AI agent risk, with no product pitch yet. Build credibility in the space before we pitch.
- Identify 20 target companies (Series B–D B2B SaaS, known to be deploying AI tooling). Research who the CTO or VP Eng is.
- Prepare the open source repo for public visibility: clean README, architecture diagram, self-hosted quickstart, clear statement of what's open vs. what's Nuvrail Cloud.

**Success metric:** 500 waitlist signups before launch. At least 10 from target company list.

### Phase 1 — Developer Preview (Milestone 2 complete)
Goal: Get 10 technical teams using the product and telling us what breaks. Build developer press credibility before Phase 2.

**Actions:**
- Show HN post: *"Show HN: Nuvrail — IMAP/SMTP approval gateway for AI agents (like git, but for email)"*. Link to GitHub repo + demo video.
- Direct outreach to 20 target companies. Personal email from Jack (CEO). Not a pitch — an invitation: "We built the thing you've been trying to build with Zapier. Would you be willing to try it?"
- Post demo video on X, LinkedIn, and relevant Discord/Slack communities (AI agent builders, developer tools).
- Offer white-glove onboarding for first 10 teams. Stella or KC on a call. Learn everything.
- Pitch developer newsletters: The Changelog, TLDR AI, Console.dev. Developer credibility built here makes the Phase 2 business press pitch stronger.

**Success metric:** 10 teams actively using the product. At least 3 willing to give a quote. At least 1 developer newsletter feature.

### Phase 2 — Public Launch (Milestone 3 complete)
Goal: Establish Nuvrail as the default answer to "how do you give AI agents email access safely?"

**Actions:**
- Full product launch: website, press outreach, Hacker News, Product Hunt.
- Press: pitch to The Information, TechCrunch, and AI-focused outlets (Import AI, The Batch). Story angle: "The $45M gap — why Nylas built the rails for AI email but forgot the brakes."
- Publish technical deep-dive: how the IMAP proxy works, how EXPUNGE blocking works, how the audit log is immutable. Target: developers who want to understand the architecture.
- Analyst briefings: Gartner (AI governance), Forrester (zero-trust for AI). We are early for these audiences but planting the flag now matters.
- Customer spotlight: one of our Phase 1 teams tells their story publicly.

**Success metric:** 1,000 signups in launch week. Coverage in at least 2 tier-1 outlets. One customer quote we can use on the website.

---

## 5. Content Plan

### Tier 1 — Foundational (write once, use everywhere)

| Content | Format | Audience | Purpose |
|---|---|---|---|
| "Email has no git" | Long-form post (1,500 words) | Developers / Hacker News | Founding insight in developer language; git framing, Show HN angle; drives waitlist signups from technical audience |
| "The contractor analogy" | Long-form post (1,200 words) | CTOs / CISOs / LinkedIn | Same founding insight in risk language; contractor with a master key framing; what happens when there's no check-in process; drives enterprise inbound |
| Product explainer video | 3-minute demo | Both | Shows the approval flow end-to-end; front page of website |
| Architecture one-pager | PDF / web page | CTO / CISO | Something they can review internally and share with their security team |
| Integration guide | Technical docs | Developers | How to point any agent at Nuvrail in under 15 minutes |

These two founding posts are the same story told to two different people. The developer version leads with the git analogy and the missing infrastructure layer. The enterprise version leads with the risk — a real scenario of what goes wrong with unrestricted AI email access — and lands on Nuvrail as the check-in process. Neither version mentions the other's framing. Each should feel written for exactly one reader.

### Tier 2 — Launch assets

| Content | Format | Purpose |
|---|---|---|
| Landing page | Website | Waitlist capture, then conversion |
| Press one-pager | PDF | Journalists and analysts |
| Show HN post | Text | Developer community launch |
| Product Hunt listing | Product Hunt page | Broader developer / maker audience |

### Tier 3 — Ongoing (post-launch cadence)

| Content | Cadence | Notes |
|---|---|---|
| Blog post: AI agent risk case study | Monthly | Real examples of what happens when there's no approval layer |
| Technical deep-dive | Monthly | Architecture, edge cases, design decisions — written for engineers |
| X/Twitter / LinkedIn | 3–4x/week | Short takes on AI agent trust, email security, product updates |
| Customer story | Quarterly | One team's experience; their words, not ours |

### Content voice reminders
- Lead with the problem, land on the solution
- Use the PR analogy early
- Name the fear, then solve it
- No buzzwords; no "revolutionary"; no "users"
- Plain language — if a sentence sounds like a press release, rewrite it

---

## 6. Developer Relations

Nuvrail's growth is developer-led. The enterprise buyer says yes only after their engineering team has evaluated the product. Developer trust is the prerequisite for everything else.

### Open Source Strategy

The core of Nuvrail — the IMAP/SMTP proxy, the staging queue, the audit log schema — is open source. This is not a charity decision; it's the primary distribution and trust mechanism.

**What's open source:**
- The IMAP and SMTP proxy servers
- The staging queue and approval logic
- The audit log schema and query interface
- Self-hosted deployment via Docker Compose (single command)

**What's Nuvrail Cloud (the business):**
- Managed hosting — we run it, monitor it, handle uptime
- OAuth integrations with Gmail, Outlook, and others
- The iOS/Android approval app
- Multi-account and team features (shared audit trail, role-based approval)
- SLAs, support, and enterprise security features

**The conversion path:** A developer finds the repo, runs it locally, shows their CTO. The CTO says "great, but we're not running our own infrastructure." That's the sale. Open source is the top of funnel; the managed service captures the revenue.

### Principles
- Docs are a product, not an afterthought. The integration guide must be completable in under 15 minutes.
- Be honest in public about limitations and edge cases. Developers notice when companies hide the hard parts.
- Show the architecture. The SPEC.md is already strong developer communication — a public version belongs in the repo on day one.

### Community
- Primary channel: GitHub (issues, discussions, public roadmap)
- Secondary: a Discord server for early adopters and agent builders
- Tertiary: Slack communities where agent builders already are (e.g., LangChain, OpenAI developer community)

### Developer outreach targets (first 30 days post-launch)
- Teams building AI email agents on top of LangChain, CrewAI, AutoGen
- Founders who have posted publicly about the difficulty of safe email access for AI
- Engineers who have shared DIY approval bot setups on GitHub or Hacker News

---

## 7. PR & Analyst Strategy

### Story angles (in priority order)

1. **The gap Nylas left open.** $45M raised to build AI email infrastructure. The approval layer is still missing. That's the hook — but no journalist writes a piece from a hook alone.
2. **The founding story.** "We built this because we watched an AI agent have unrestricted email access and it was alarming." Personal stakes are what make this a story rather than a product announcement. The Nylas gap is the context; the founding story is the reason anyone cares. These two belong together in every pitch.
3. **The DIY problem.** Engineers are duct-taping together Zap + Gmail + Slack to solve a problem that deserves a real product. Good for developer-focused outlets; relatable and specific.
4. **Open source trust.** The core is public. You can read exactly how every operation is staged and logged. That's not typical for infrastructure that handles your email — and it's the point.
5. **The governance angle.** As AI regulation increases (EU AI Act, emerging US standards), companies will need audit trails for AI actions. Nuvrail is the audit trail for email. Longer arc — relevant for analyst briefings more than launch press.

### Target outlets by phase

**Phase 1 (Developer Preview) — build developer credibility first:**
- The Changelog podcast / newsletter
- TLDR AI, The Rundown
- Hacker News (Show HN)
- Console.dev (developer tools newsletter)
- AI-focused: Import AI (Jack Clark), The Batch (Andrew Ng)

Developer press credibility is what makes the Phase 2 business press pitch land. "Already covered by The Changelog and featured on HN front page" is a better opening than a cold pitch.

**Phase 2 (Public Launch) — business and enterprise press:**
- TechCrunch, The Information
- The Register, InfoQ
- Analyst firms: Gartner (AI governance track), Forrester (zero-trust AI), 451 Research

### Timing
- Developer newsletter pitches: begin at Phase 1 launch
- Analyst briefings: begin 6 weeks before Phase 2 launch (give them time to write)
- Business press embargo: lift day of Phase 2 launch, coordinated with Show HN and Product Hunt

---

## 8. Customer Success

For a trust-first product, onboarding is not an operational detail — it's part of the product. An enterprise buyer who doesn't feel safe in the first 10 minutes will never become a reference customer. A reference customer is worth more than any press mention.

### The first 30 days

**Days 1–3: Connection and orientation**
- Personal welcome from Stella or KC (not a drip sequence)
- Confirm the agent is connected through Nuvrail and operations are staging correctly
- Walk through the audit log together — show them what it looks like when the AI proposes an action
- Answer the CISO question before they ask it: here's exactly what we log, where it lives, and how to export it

**Days 4–14: First real use**
- Customer runs their AI agent against real email with Nuvrail in the loop
- We are on Slack or email, same-day response, for any friction
- We document every question they ask — these become FAQ entries, docs improvements, or product fixes
- If they hit an edge case, we fix it or explain the workaround personally

**Days 15–30: Value confirmation**
- Short check-in: what's working, what's not, what would make this a no-brainer to expand?
- If they're happy: ask for a quote and whether they'd participate in a case study
- If they're not: understand why before they churn — this is the most valuable conversation we can have

### Reference customer goal

Every Phase 1 customer is a potential reference customer. By 90 days post-launch, we want at least one customer who will:
- Take a call from a prospect
- Let us publish their story (anonymized if needed)
- Give us a quote we can use on the website

Reference customers close enterprise deals faster than any piece of content we can write. Invest accordingly.

---

## 9. Metrics & Success Criteria

### Pre-launch
- Waitlist signups: **500 target**
- Target company contacts captured: **20**
- Blog post: **10,000 views**, submitted to HN front page

### Developer Preview
- Active teams using product: **10**
- Customer quotes collected: **3**
- GitHub stars: **500**

### Public Launch (30 days post-launch)
- Signups: **1,000**
- Press mentions in tier-1 outlets: **2**
- Developer community members (Discord/GitHub): **500**
- Inbound enterprise inquiries: **5**

### 90 days post-launch
- Paying customers or pilots: **3**
- NPS from developer preview cohort: **40+**
- "Nuvrail" mentioned unprompted in AI agent developer discussions: **qualitative signal, tracked manually**

---

## 10. Budget Priorities (Seed Stage)

We are not spending money to amplify a message we haven't validated. The pre-launch and developer preview phases should run on near-zero paid spend.

**Where to spend:**
1. **Docs and developer experience** — if anything gets external investment pre-launch, it's making the integration guide fast and the demo video compelling
2. **Event presence** — one developer conference where AI agent builders are present (AI Engineer World's Fair, or equivalent); attend before we sponsor
3. **Content production** — the product explainer video is worth spending on; everything else can start rough

**Where not to spend:**
- Paid social (too early; we don't know what converts yet)
- SEO agencies (do the work ourselves first; learn what terms matter)
- PR firms (personal outreach from Jack will outperform an agency at this stage)

---

## 11. Pricing Model

All board decisions resolved. No open questions.

### Tiers

| Tier | What you get | Price |
|---|---|---|
| **Self-Hosted** | Full open source, community support, unlimited everything | Free |
| **Cloud Starter** | 1 email account, 1 AI agent, 30-day audit history | Free (freemium) |
| **Cloud Pro** | Up to 5 accounts, 5 agents, unlimited audit, auto-approval rules, Web Push | $29/account/month |
| **Cloud Team** | Unlimited accounts/agents, shared audit trail, role-based approvals, SSO, SLA | $199/month flat (up to ~10 accounts) or custom above |

### Why this structure works for marketing

**Self-Hosted → Cloud Starter** is a no-friction conversion. A developer who runs the open source version and wants to skip managing infrastructure upgrades to Cloud Starter without a procurement conversation.

**Cloud Starter → Cloud Pro** is the Priya conversion. She starts free, her enterprise prospect asks "how do we deploy this at scale?", and the answer is Cloud Team — with Priya as the champion inside the deal.

**Cloud Team** is the enterprise sale. Flat $199 removes the "how much will this cost us?" anxiety from the initial conversation. Custom pricing above ~10 accounts keeps the door open for large deployments.

### Marketing implications

- The freemium tier means our developer outreach has a zero-friction CTA: "Sign up free, no credit card."
- Self-Hosted remains the trust anchor — we never hide the product behind a paywall
- Pricing page copy should lead with Self-Hosted to establish credibility, then present Cloud tiers as "let us run it for you"
- $29/account/month is defensible on value: one prevented incident (wrong email sent by an agent, client relationship damaged) is worth more than a year of Pro

### Resolved board decisions

| Question | Decision |
|---|---|
| Open source? | Yes — IMAP/SMTP proxy, staging queue, audit log. Managed service is the business. |
| SMTP at launch? | Yes — IMAP and SMTP launch together. |
| Nuvrail brand vs. product name? | Nuvrail is both the company and the product. One name. |
| Pricing model? | Four-tier structure above. Numbers to be refined before Phase 2 launch. |

---

*Next step: board review and feedback. Once aligned, I'll develop the landing page brief and the "email has no git" blog post as first deliverables.*

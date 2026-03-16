# Nuvrail — Marketing & Communications Plan

**Status:** Draft v1  
**Date:** March 16, 2026  
**Author:** Stella (CMO)  
**Inputs:** MarketingEthos.md (v2), SPEC.md

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

The approval gateway between AI agents and real inboxes. Provider-agnostic. Agent-agnostic. Every proposed action staged, reviewed, logged.

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

### Trust proof points (in priority order)
1. **No permanent deletes** — the gateway blocks EXPUNGE; nothing is gone until you say so
2. **Immutable audit log** — every proposed, approved, and rejected action is recorded
3. **Provider-agnostic** — works with Gmail today; designed to work with any IMAP server
4. **Agent-agnostic** — the AI sees standard IMAP; Nuvrail requires no agent modifications
5. **Human-readable diffs** — approvals show plain language, not raw commands

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
**What they need from us:** A drop-in approval layer they can point to. Fast integration, clear docs, no need to build it themselves.  
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

**Success metric:** 500 waitlist signups before launch. At least 10 from target company list.

### Phase 1 — Developer Preview (Milestone 2 complete)
Goal: Get 10 technical teams using the product and telling us what breaks.

**Actions:**
- Show HN post: *"Show HN: Nuvrail — IMAP approval gateway for AI agents (like git, but for email)"*. Link to GitHub repo + demo video.
- Direct outreach to 20 target companies. Personal email from Jack (CEO). Not a pitch — an invitation: "We built the thing you've been trying to build with Zapier. Would you be willing to try it?"
- Post demo video on X, LinkedIn, and relevant Discord/Slack communities (AI agent builders, developer tools).
- Offer white-glove onboarding for first 10 teams. Stella or KC on a call. Learn everything.

**Success metric:** 10 teams actively using the product. At least 3 willing to give a quote.

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

| Content | Format | Purpose |
|---|---|---|
| "Email has no git" | Long-form post (1,500 words) | Establishes the founding insight; drives waitlist |
| Product explainer video | 3-minute demo | Shows the approval flow end-to-end; front page of website |
| Architecture one-pager | PDF / web page | Gives CTOs and CISOs something to review internally |
| Integration guide | Technical docs | Tells developers how to point their agent at Nuvrail in 15 minutes |

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

Nuvrail's growth is developer-led. The enterprise buyer says yes only after their engineering team has evaluated the product. This means developer trust is the prerequisite for everything else.

### Principles
- Open source what makes sense to open source (the IMAP proxy layer is a strong candidate; discuss with KC)
- Docs are a product, not an afterthought. The integration guide must be completable in under 15 minutes.
- Be honest in public about limitations and edge cases. Developers notice when companies hide the hard parts.
- Show the architecture. The SPEC.md is already a strong piece of developer communication — a version of it should be public.

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

1. **The gap Nylas left open.** $45M raised to build AI email infrastructure. The approval layer is still missing. That's the story.
2. **The DIY problem.** Engineers are duct-taping together Zap + Gmail + Slack to solve a problem that deserves a real product.
3. **The governance angle.** As AI regulation increases (EU AI Act, emerging US standards), companies will need audit trails for AI actions. Nuvrail is the audit trail for email.
4. **The founding story.** The insight came from actually using AI agents with email access and watching them act without guardrails. Personal stakes make a good story.

### Target outlets
- **Developer-focused:** Hacker News, The Register, InfoQ
- **AI-focused:** Import AI (Jack Clark), The Batch (Andrew Ng), TLDR AI, The Rundown
- **Business/enterprise:** TechCrunch, The Information, Protocol (if revived)
- **Analyst firms:** Gartner (AI governance track), Forrester (zero-trust AI), 451 Research

### Timing
- Analyst briefings: begin 6 weeks before public launch (give them time to write)
- Press embargo: lift day of launch, coordinated with Show HN and Product Hunt

---

## 8. Metrics & Success Criteria

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

## 9. Budget Priorities (Seed Stage)

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

## 10. Open Questions for the Board

1. **Open source?** Should the IMAP proxy layer be open source? This is a significant trust signal for developers and could drive organic distribution. Needs a conversation with KC about what's defensible if the core is open.
2. **Pricing model?** We haven't defined this. Enterprise infrastructure typically goes usage-based (per operation, per seat, or per account). Need to decide before Phase 2 launch.
3. **SMTP scope and timeline?** The spec notes SMTP comes after IMAP. Should we announce SMTP support as a roadmap item at launch, or stay quiet until it's ready?
4. **"Nuvrail" brand vs. product name?** Is Nuvrail the company and the product? Or should the product have its own name? Simpler to keep them unified at this stage.

---

*Next step: board review and feedback. Once aligned, I'll develop the landing page brief and the "email has no git" blog post as first deliverables.*

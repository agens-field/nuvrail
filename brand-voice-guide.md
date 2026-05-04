# Nuvrail Brand Voice Guide — v1

**Status:** Draft, pending board approval of category claim  
**Author:** Stella (CRO)  
**Date:** 2026-05-04  
**Input:** MarketingEthos.md v2, MarketingPlan.md v3, positioning-audit.md  
**Effective:** Upon board sign-off, target 2026-05-20  

---

## The One Thing

This is not a style preference. It is a constraint. Every piece of external communication — website copy, HN post, press pitch, social post, technical doc intro, sales email — must be traceable to this:

> **Nuvrail is the pull request model for AI email access. Nothing reaches your inbox without a human approving the diff.**

If content cannot be traced to this, we don't publish it.

---

## What We Are

The approval gateway between AI agents and real inboxes. Provider-agnostic. Agent-agnostic. Every proposed email action staged, reviewed, logged immutably before execution. Open-source core. Human always in control.

## What We Are Not

Say these explicitly when needed. Never hedge:

- Not a premium email client
- Not a workflow automation tool (not Zapier, not Make, not n8n)
- Not an AI assistant
- Not a closed ecosystem
- Not an agent — we are the layer that makes agents trustworthy

---

## Category Claim

**⚠️ PENDING BOARD DECISION — see fork on Brand & Positioning path**

Category placeholder for this draft: *AI email governance layer*

This will be updated within 24 hours of the board's decision. All launch assets referencing the category should use the approved label. Until the fork resolves, use the descriptor ("the approval gateway between AI agents and your inbox") rather than the category label in any external copy.

---

## The Two Approved Metaphors

These are the only two analogies we use. Do not introduce new ones without updating this guide.

### 1. The Git / Pull Request Analogy — developer-facing

Use this with: HN, GitHub, technical docs, developer newsletters, Priya, Rodrigo.

> "Git made AI coding agents trustworthy because you see the diff before it merges. Email has no equivalent. Nuvrail is that equivalent. Every approval is a diff. Every audit log entry is a commit."

Mechanics that make this analogy work:
- The agent proposes an action → like opening a PR
- The staging queue holds it → like a branch waiting for review
- The human approves or rejects → like merging or closing the PR
- The audit log captures everything → like git history, immutable

Do not stretch the analogy beyond these four points. Do not use git vocabulary (commits, branches, merges) in non-developer contexts.

### 2. The Tesla Center Screen Analogy — enterprise / non-developer facing

Use this with: press, CTOs, analysts, enterprise sales, Dana.

> "Tesla Autopilot has higher engagement than Volvo or Audi lane assist because Tesla shows you what it sees — other cars rendered on screen in real time. Visibility is the mechanism of trust, not just a feature. Nuvrail is that visualization layer for AI agents acting on your email."

The insight: humans trust systems more when they can see what the system is perceiving and doing. Showing the work builds trust more durably than asserting trustworthiness. This is why the approval queue is a feature, not a concession.

Do not use this analogy in developer contexts. A developer who has read the git analogy will find the Tesla version redundant or soft. Use one; use it for the right audience.

---

## Two Voice Registers

Nuvrail speaks to two audiences who require different registers. The product is the same. The vocabulary is not.

### Register 1 — Developer / Technical (Priya, Rodrigo, HN, GitHub, TLDR AI)

| Attribute | What it means |
|---|---|
| Peer-to-peer | Write as an engineer talking to another engineer. Not a vendor pitching a buyer. |
| Architecture-forward | Show how it works. The IMAP proxy, the staging queue, the audit log format. Technical readers trust implementation, not claims. |
| Honest about tradeoffs | This adds latency. Say so. Honest acknowledgment of limits is more credible than hedging. |
| No pitch language | "Our solution," "we help you," "empower," "seamlessly" — none of these. |
| Show the code | Reference the open-source repo early. The code is the credential. |

**Vocabulary to use:** proxy, intercept, staging queue, EXPUNGE, IMAP, SMTP, audit log, diff, credentials, endpoint, open source, self-host

**Vocabulary to avoid:** solution, platform, revolutionary, AI-powered, seamless, empower, leverage, unlock

**Sample sentence (good):** "Nuvrail intercepts the EXPUNGE before it reaches the server. The agent's delete becomes a staged action; nothing is permanently removed until a human approves it."

**Sample sentence (bad):** "Our AI-powered platform empowers your team to leverage email automation safely."

---

### Register 2 — Enterprise / Executive (Dana, CTO, CISO, press, analysts)

| Attribute | What it means |
|---|---|
| Risk-first | Lead with the fear. Name what goes wrong before naming the solution. The CTO who's been asked to greenlight AI email access is not looking for a pitch — they're looking for a reason to feel safe saying yes. |
| Governance vocabulary | Audit trail, approval gate, compliance, accountability, paper trail, sign-off. These are the words that live in the CTO's head when they think about this problem. |
| Peer-to-peer, not vendor | Write as a CTO talking to another CTO presenting a framework. Not a sales deck. |
| Concrete organizational risk | "When your CEO asks what the agent did, can you answer?" is more effective than "gain visibility into AI actions." |
| The Tesla frame | Visibility is the mechanism of trust. We don't just add an audit log — we make AI email action visible and reviewable in real time. |

**Vocabulary to use:** approval gate, audit trail, governance, accountability, sign-off, immutable log, policy, compliance, visibility, oversight

**Vocabulary to avoid:** cool, interesting, developer-friendly, hackable, open source (lead with it, don't bury it — but it's not the primary trust signal for this audience)

**Sample sentence (good):** "When something goes wrong — and it will — and your CEO asks 'what did the agent do in March?' you should be able to answer that question. Nuvrail's immutable audit log means you can."

**Sample sentence (bad):** "Nuvrail gives enterprises visibility into their AI email workflows."

---

## The Three Personas and How to Talk to Each

### Dana — "The Cautious CTO" (primary enterprise target)

**Role:** CTO at a 150-person B2B SaaS. Has been asked to greenlight AI email access.  
**Fear:** Raw IMAP + something goes wrong = career moment.  
**What she needs to hear:** That there is a formal approval layer with an audit trail she can show her CISO. That if something goes wrong she has a record. That it's not locked to one provider or agent.  
**Register:** Enterprise/executive (Register 2)  
**Channels:** LinkedIn, engineering blogs, peer recommendations, analyst reports  
**Angle:** Risk management. The approval layer as the answer to "what if it does something wrong?"

**Headline that works for Dana:** *"You Wouldn't Give a Contractor a Master Key. Why Are You Giving One to Your AI?"*

**Pitch that works for Dana:** One paragraph, risk-first, closes with "immutable audit log" and "no permanent deletes without approval."

---

### Priya — "The Agent Builder" (primary developer target)

**Role:** Founding engineer building an AI email automation agent. First enterprise prospect asked "what happens if the agent makes a mistake?"  
**Fear:** Losing the deal because she can't credibly answer the trust question.  
**What she needs to hear:** Drop-in integration, no SDK changes, free to start, fast path to enterprise-readiness.  
**Register:** Developer/technical (Register 1)  
**Channels:** GitHub, TLDR AI, Discord developer communities, HN  
**Angle:** "We solved the problem you were going to have to build yourself."

**Headline that works for Priya:** *"Show HN: Nuvrail — IMAP/SMTP approval proxy for AI agents (open source)"*

**Pitch that works for Priya:** Show the endpoint config. Two paragraphs max. Link to GitHub first.

---

### Rodrigo — "The DIY Escapee" (near-term conversion target)

**Role:** Senior engineer who built a Zap + Gmail + Slack approval bot. It works until it doesn't.  
**Fear:** Something will go wrong and he won't be able to explain what happened.  
**What he needs to hear:** That his DIY setup has the specific gaps Nuvrail closes (no audit log, breaks on edge cases, no EXPUNGE blocking). And that migration doesn't require ripping everything out.  
**Register:** Developer/technical with a peer acknowledgment that what he built was reasonable  
**Channels:** HN, Reddit (r/devops, r/MachineLearning), Slack communities  
**Angle:** "You already solved the easy part. Here's the part your bot can't handle."

**Headline that works for Rodrigo:** *"The part your Zapier approval bot can't do: immutable audit log, EXPUNGE blocking, and a migration path."*

**Pitch that works for Rodrigo:** Acknowledge that what he built was smart given what was available. Be specific about what it can't do. Offer a concrete path forward.

---

## Tagline

**Primary:** *The approval layer between AI agents and your inbox.*

This is the default tagline. Website header, press materials, one-sentence description.

**Alternate — developer contexts:** *IMAP/SMTP approval proxy for AI agents.*

More literal. Less poetic. Use on GitHub, HN, technical docs. This audience is skeptical of tagline language.

**Alternate — punchy / social:** *Nothing reaches your inbox without your approval.*

Works for social posts and short-form. Makes the value proposition the benefit, not the mechanism.

Do not use "AI agents for email. With a human in the loop." — it sounds like we are the agent, not the layer that governs the agent. Retired.

---

## Copy Principles (house style)

These are rules, not preferences:

1. **Cut everything unnecessary.** If a word does not add meaning, remove it. A sentence that survives three cuts is usually the right sentence.

2. **Simple, concrete words.** Prefer "delete" over "remove," "approve" over "authorize," "email" over "communications," "record" over "document." When in doubt: what word would a person use when talking to a friend?

3. **Lead with the problem, land on the solution.** Name the fear before you pitch the fix. The reader who recognizes the fear is already halfway to a conversion.

4. **Name the fear explicitly.** "AI agents with raw IMAP access can permanently delete messages — and there is no undo" is more effective than "AI agents may create unintended consequences." The specific version is scary in a productive way. The vague version is forgettable.

5. **No buzzwords.** The word "revolutionary" has never appeared in our materials and never will. Same list: "innovative," "cutting-edge," "game-changing," "disruptive," "synergy," "leverage" (as a verb), "ecosystem" (unless referring to a literal technical ecosystem), "empower."

6. **Customer language beats company language.** If a CTO says "I'm worried about what the agent will do when I'm not watching," that phrase is more valuable than anything we can invent. Use it. Attribute it.

7. **"Teams, customers, buyers" — never "users."** The people we serve are decision-makers whose trust is the product. "Users" flattens that. Dana is not a user. She is a CTO making a governance decision.

8. **Be honest about tradeoffs.** Nuvrail adds a review step. That step is the product. We do not apologize for it and we do not minimize it. "Yes, this adds latency. That's the point." is the right answer.

9. **State confidence explicitly.** When we're uncertain about something — a competitive claim, a market size, a product capability — we say so. We do not assert things we cannot defend.

10. **One reader.** Every piece of content should be written for exactly one reader in exactly one situation. "Email has no git" is written for an engineer on HN at 8pm. "You wouldn't give a contractor a master key" is written for a CTO who just got asked by their CEO to approve AI email access. They are not the same person. Do not write for both in the same piece.

---

## Competitive Positioning (what to say when they come up)

**When asked about Nylas:**
> "Nylas built excellent IMAP abstraction — they raised $45M doing it. But the approval layer doesn't exist in their stack. They give AI agents the rails; we're the signal box."

Do not trash Nylas. Acknowledge the real work they did. The gap is honest and the analogy is clean.

**When asked about Microsoft Copilot / Google Workspace AI:**
> "If you're all-in on Microsoft or Google and you're comfortable with approval being on their terms, in their ecosystem, those are reasonable tools. We built for teams that need approval to be independent of the email provider — and auditable by someone other than the provider."

Do not trash Microsoft or Google. The insight (closed ecosystem, approval on their terms) is the real differentiator, not a quality comparison.

**When asked about Zapier / Make / n8n:**
> "What your team built with Zapier was a reasonable solution to the problem before Nuvrail existed. The gaps are specific: no EXPUNGE blocking, no immutable audit log, and it breaks in ways that are hard to diagnose when the AI does something unexpected. We're the version built for the AI agent use case specifically."

Acknowledge the DIY solution with respect. Rodrigo is our friend; don't insult his bot.

---

## What a Correct Nuvrail Sentence Sounds Like

**Good:**
- "AI agents with raw IMAP access can permanently delete messages. There is no undo."
- "Every proposed action is staged. You approve it or you don't. Either way, it's logged."
- "We never read your email. The proxy inspects the IMAP command, not the content."
- "The core is open source. Read the code. Run it yourself."
- "We work with any agent and any provider. We have no incentive to lock you in."

**Bad:**
- "Nuvrail empowers enterprises to leverage AI email automation securely."
- "Our innovative platform provides seamless approval workflows for your team."
- "With Nuvrail, you can unlock the full potential of AI agents while maintaining oversight."
- "We're revolutionizing how businesses manage AI-assisted communication."

---

## Review and Update Cadence

This guide is reviewed quarterly. First review: Q3 2026 (approx. 2026-09-15).

Triggers for an off-cycle review:
- A competitive shift that materially changes the positioning landscape
- Customer language patterns from onboarding calls or feedback that are better than ours
- A board decision that changes what we are or what we offer
- The category claim decision (this guide should be updated within 24 hours of the board's fork resolution)

Owner: CRO. Board receives the output of each quarterly review.

---

*Category claim placeholder will be replaced upon board decision. See fork on Brand & Positioning path in Ripple Path.*

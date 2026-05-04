# Positioning Audit: MarketingEthos.md vs. MarketingPlan.md

**Prepared by:** Stella (CRO)  
**Date:** 2026-05-04  
**Purpose:** Input to brand voice guide v1. Identify consistent claims to carry forward, conflicts to resolve, and language patterns worth keeping.

---

## What's locked and consistent

These claims appear in both documents, are phrased consistently, and should be ported directly into the brand voice guide without debate:

1. **The one thing:** "Nuvrail is the pull request model for AI email access. Nothing reaches your inbox without a human approving the diff." — verbatim agreement across both docs.

2. **What we are not:** Not a premium email client, not a workflow automation tool, not an AI assistant, not a closed ecosystem. Both docs agree.

3. **The git/diff analogy:** Both docs use it as the primary explanatory frame. It's doing real work — don't replace it, don't add qualifiers to it.

4. **Competitive frame:** Nylas gap ($45M raised, no approval layer), MS Copilot/Google (closed ecosystems), Zapier/Make (DIY, fragile, unaudited). Agreement across both docs.

5. **Copy principles:** Direct, plain, no buzzwords, no "revolutionary." Lead with the problem. Name the fear. Both docs agree, Paul Graham "Writing, Briefly" is the implicit guide.

6. **Open source as trust mechanism:** Both docs frame open source as distribution and trust, not charity. Agreement on what's open (proxy, staging queue, audit log) vs. closed (managed service).

7. **"Teams, customers, buyers" — not "users."** Both docs agree. Enforce this.

---

## Conflicts to resolve

### 1. Who is the primary audience? (Medium priority)

**MarketingEthos.md:** "The enterprise buyer responsible for AI deployment — CTO, CISO, VP Engineering." Enterprise-first framing throughout.

**MarketingPlan.md:** Primary persona is Dana, "CTO at a 150-person B2B SaaS company." This is mid-market, not enterprise. The plan also gives equal or greater emphasis to Priya (founding engineer / agent builder) as the organic growth driver.

**The conflict:** If we say "enterprise" in the brand guide, our voice and content tier toward compliance, audit, and org-level risk. If we say "mid-market technical buyer" and "developer-first," we tier toward speed, simplicity, and integration. These are different voices for different readers.

**Recommended resolution:** The brand guide should define two distinct voice registers: (a) enterprise-adjacent copy for website, press, and sales collateral — CISO-friendly, risk-framed; (b) developer-facing copy for GitHub, HN, newsletter pitches, and docs — peer-to-peer, architecture-forward. Both are Nuvrail. They should not sound identical.

---

### 2. Rodrigo (Persona 3) is in MarketingPlan but not in MarketingEthos (Low priority, but flag it)

**MarketingPlan.md** defines Rodrigo — "The DIY Escapee" — as a near-term conversion target. Engineers who already built a Zap+Gmail+Slack bot and know it's fragile.

**MarketingEthos.md** has no third persona. Its secondary audience is generic ("AI developer building agents that touch email").

**Recommended resolution:** Add Rodrigo to the brand guide as a recognized persona. He is actually the most conversion-ready segment we have — he already feels the pain and has already tried to solve it. The content angle for Rodrigo ("you already know the problem exists — here's a real answer") is different from both Dana and Priya.

---

### 3. Tesla analogy missing from both documents (Medium priority)

The Tesla analogy — "Tesla Autopilot shows you what it sees (other cars on screen), which is why people trust it more than Volvo lane assist. Visibility is the mechanism of trust, not just a feature" — is in the company context and in my memory, but appears in neither MarketingEthos.md nor MarketingPlan.md.

This analogy is strong for a non-technical audience (press, enterprise buyers, board presentations). It belongs in the brand guide alongside the git analogy.

**Recommended resolution:** Add the Tesla analogy to the brand guide as the second approved metaphor, positioned as the enterprise/press-facing frame (vs. the git analogy, which is the developer-facing frame).

---

### 4. Category claim: missing from both documents (High priority — this is the open fork)

Neither document names a category. MarketingPlan.md is the closest: "The approval gateway between AI agents and real inboxes" — but that's a descriptor, not a category label.

This is the open decision fork I filed on the Brand & Positioning path. Until resolved, the brand guide carries a placeholder. The voice guide can be written around it, but it cannot be finalized.

---

### 5. Tagline variants unresolved (Low priority pre-launch, medium priority for voice guide)

**MarketingPlan.md** lists three tagline options for A/B testing:
- "The approval layer between AI agents and your inbox."
- "AI agents for email. With a human in the loop."
- "Nothing reaches your inbox without your approval."

**MarketingEthos.md** doesn't mention tagline testing — it implies the positioning statement *is* the tagline.

**Recommended resolution:** The voice guide should name one primary tagline and note the variants as acceptable alternates for specific contexts (e.g., the second variant works well on social; the third works well on the landing page hero). Don't run a true A/B test at launch — we don't have the traffic to get signal fast enough. Pick one, use it consistently, revisit at 30 days.

**My pick:** "The approval layer between AI agents and your inbox." It's the clearest. It names who it's for (people with AI agents), what it does (approval), and where it does it (inbox). The other two are hooks, not positioning.

---

## Language patterns worth keeping from each document

**From MarketingEthos.md:**
- "We name the fear. We solve it." — great internal compass; worth putting in the voice guide verbatim.
- "The product speaks. Our job is to make sure the right person understands what it does." — useful reminder for anyone writing copy; prevents over-explaining.
- "We do not apologize for it." (re: adding a step to workflows) — this is real confidence; keep it.

**From MarketingPlan.md:**
- "Every write is a diff, every approval is a commit, every action is logged forever." — the most compressed, precise description of the product. Use this in the technical docs and the developer-facing pitch.
- "We have no incentive to lock you in — and that's the point." — agent-agnostic message, works hard in enterprise conversations.
- "Self-Hosted remains the trust anchor." — this framing for open source is better than "we're open source." Anchor is a better word than available.

---

## Summary for brand voice guide input

| Area | Status | Action |
|---|---|---|
| The one thing | Locked | Port verbatim |
| What we are not | Locked | Port verbatim |
| Git/diff analogy | Locked | Primary developer frame |
| Tesla analogy | Missing from both docs | Add to voice guide |
| Competitive frame | Locked | Port verbatim |
| Copy principles | Locked | Port verbatim |
| Primary audience definition | Conflict | Resolve with two-register approach |
| Rodrigo persona | Missing from MarketingEthos | Add to voice guide |
| Category claim | Open fork | Placeholder in voice guide until board decides |
| Tagline | Unresolved variant | Recommend primary: "The approval layer between AI agents and your inbox." |

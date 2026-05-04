# Onboarding Call Script — First 10 Teams
**Who runs this call:** Jack or KC  
**When:** Within 5 days of a team's first login to Cloud Starter  
**Duration:** 30 minutes  
**Purpose:** Product research that also improves retention. This is not a sales call.

---

## Before the Call (5 minutes of prep)

Pull these facts before you dial. The call goes much better when you know who you're talking to.

**What to look up:**

1. **What they connected.** Which inbox provider? Gmail, Outlook, something else? Did they connect one account or multiple?
2. **What happened in their first session.** Did they actually get to the approval queue? Did they hit an error? Did they complete setup or drop off?
3. **What they signed up with.** Their email domain tells you a lot. Indie hacker vs. startup vs. mid-market company have very different needs.
4. **Whether they've seen the auto-approval rules UI.** Check if they've opened it. If they haven't, they may not know it exists — and it's the answer to their most likely objection.
5. **Any support tickets or error logs.** Don't let them tell you about a bug you already know about.

**Frame the call for yourself before you get on:**  
You are not trying to impress them. You are trying to understand what's actually happening when they use the product. Bring curiosity, not a pitch.

---

## Opening (2 minutes)

The goal: make it clear this is not a sales call before they have a chance to get their guard up.

> "Hey [name], thanks for making time. Quick framing before we start: this call is not a sales pitch. You're one of the first [ten / fifteen] teams using Nuvrail and I genuinely want to understand what's working and what's broken. We'll do a quick check that everything is actually running on your end, then I want to ask you some specific questions about your experience. You can be completely honest — bad news is more useful to us than good news right now."
>
> "Does that sound good?"

Then: shut up and let them respond. Some people will laugh and say yes. Some people will ask a clarifying question. Either way, you've set the right tone.

**Do not rush through this.** Thirty seconds of silence after your framing question is fine. Let them land.

---

## Technical Check (10 minutes)

Confirm that the product is actually working for them before you ask them anything about their experience of it. A user who thinks they've set it up but hasn't will give you garbage research data.

Walk through in this order:

**1. Inbox connected?**
> "Let's start with the basics — can you pull up Nuvrail for me? I want to confirm your inbox is connected and showing the right account."

If they're sharing screen: watch for hesitation when they try to log in. That hesitation tells you something.

**2. First approval queue.**
> "Have you had any AI-proposed actions come through yet? Let's look at your approval queue together."

If the queue is empty:
- Ask what agent they're connecting Nuvrail to (or planning to). This tells you whether they're in setup mode or actually using it.
- If they're in setup mode, walk them through generating a test action so the queue isn't empty before you end the call.

If the queue has items:
- Ask them to walk you through one approval decision. Watch how long it takes. Watch where they look. Note whether they read the full diff or just click approve.

**3. Auto-approval rules — have they seen it?**
> "Have you had a chance to look at the auto-approval rules UI? It lets you set up rules like 'auto-approve folder moves from senders I've approved before.'"

If they say no: show them where it is. Briefly. Don't demo it — just point to it.  
If they say yes: ask what they set up. This is research gold.

**4. Any errors?**
> "Has anything broken or behaved unexpectedly since you connected?"

Listen carefully. Don't interrupt. Don't immediately tell them whether it's a bug or user error — just write it down and say "that's useful, let me make sure we follow up on that."

---

## Research Questions (12 minutes)

This is the heart of the call. These are the questions that will tell you whether the product has product-market fit and where to focus next.

Ask the questions in roughly this order. Don't read them verbatim — use them as prompts.

**About the moment they signed up:**

1. > "What were you actually trying to do when you found Nuvrail? Like, what was the specific situation you were in?"

   *What you're listening for:* The concrete trigger. "I was building a thing for my boss" is different from "an AI agent sent an email I didn't mean to send." The specificity of their answer predicts how strong their need is.

2. > "Had you tried anything else before Nuvrail? What was wrong with it?"

   *What you're listening for:* Their mental model of the competitive landscape. Who do they think we're up against? If they say "nothing — there was nothing else," that's important.

**About the approval experience:**

3. > "When you saw your first proposed email action in the approval queue — what did you feel? Walk me through that moment."

   *What you're listening for:* Trust, anxiety, curiosity, boredom. The emotional response to seeing the diff is the product. If they felt relief, we have something. If they felt nothing, we have a design problem.

4. > "How long did you spend deciding whether to approve or reject the first action? What made you hesitate or not hesitate?"

   *What you're listening for:* Friction vs. confidence. If they hesitated, why? If they didn't — was it because the diff was clear, or because they just clicked through without reading?

5. > "Is there anything you wish you could see in the approval queue that isn't there right now?"

   *What you're listening for:* Missing context. Maybe they want to see the original email that triggered the reply. Maybe they want a predicted send time. Maybe they want to see the agent's reasoning.

**About workflow and team:**

6. > "Is this just you using it, or are other people on your team involved in approvals?"

   *What you're listening for:* Multi-user patterns. If more than one person needs to approve, we have a coordination problem we may not have fully solved. Also: team use is a signal toward Pro/Team tier.

7. > "How does Nuvrail fit into your existing workflow? Like, what happens before an action comes into the queue, and what happens after you approve it?"

   *What you're listening for:* Integration gaps. If their workflow requires copying something out of Nuvrail into another system, that's a friction point. If they've wired it into a Slack notification, that's a power user pattern we should be amplifying.

8. > "What's the thing that almost made you not sign up — or almost made you stop using it in the first few days?"

   *What you're listening for:* The near-miss. This is often more honest than "what don't you like." Everyone has a moment where they almost bounced. Find it.

**About trust:**

9. > "Do you feel more or less comfortable letting your AI agent touch your inbox now than you did before Nuvrail? What changed?"

   *What you're listening for:* Whether the core value proposition is landing. If they say "a lot more comfortable" — ask them to say more. That language belongs in our copy. If they say "about the same" — we have a problem.

10. > "Has anything happened in Nuvrail that you wouldn't have caught otherwise — a proposed action that you rejected that you're glad you saw?"

    *What you're listening for:* The "aha" moment. A specific story about catching a bad action is the most powerful testimonial we can get and the clearest signal of retention.

---

## Upgrade Conversation (3 minutes)

One question. Then listen. Do not pitch.

> "One last thing — if you were going to upgrade to Pro, what would have to be true? Like, what would need to happen in your work for $29 a month to be an obvious yes?"

Let them answer. Write it down exactly. Don't reframe it. Don't sell against it.

If they ask "what does Pro include?" — give them the one-line answer (longer audit log, multiple accounts, priority support) and immediately return to: "does any of that move the needle for you?"

---

## Close (3 minutes)

**What to say:**

> "This has been really helpful. One thing before I let you go — is there anything you wanted to tell us about the product that I didn't ask about?"

Then:

> "I'll send you a short follow-up within 24 hours. It'll have a link to our feedback channel — it's a Slack/Discord space where our first users can flag things directly to us. We actually read it. And if anything breaks before then, here's how to reach me directly: [email or Slack handle]."

**What NOT to do:**
- Don't promise specific features or timelines
- Don't say "we're working on that" unless you know for certain
- Don't upsell on the call itself

---

## Follow-up (within 24 hours)

Send a short email or DM. Include:

1. One specific thing you heard from them that you're taking back to the team (makes them feel heard)
2. An invitation to the #nuvrail-feedback channel
3. Your direct contact for anything urgent
4. If they reported a bug: a ticket number or confirmation that it's been logged

Template:

> Hi [name],
>
> Really glad we talked. The thing you said about [specific observation they made] is going straight to [Jack/KC] — that's exactly the kind of thing we're trying to understand right now.
>
> I'd love to add you to a small Slack space where our first users give us direct feedback. No noise, no spam — just a place to flag things and hear from us directly. I'll send you an invite separately.
>
> If anything breaks or you have questions, I'm at [contact]. Fastest response is [method].
>
> Thanks again for the time.
>
> [Name]

---

## What to Listen For — Signal Guide

Use this during and after the call. Not all signals are equal.

---

### 🔴 Churn Signals

These indicate a user who is probably not coming back:

- They haven't connected a real inbox. They set up a test account and forgot about it.
- They found the approval queue annoying without understanding why it exists. They just want the agent to act.
- They connected it but their AI agent isn't actually running email actions yet. Nuvrail is solving a problem they don't have today.
- They couldn't answer what happens after they approve an action. They're not using the downstream flow.
- When you asked about trust, they said "about the same." The core value prop didn't land.
- They mentioned they're "trying a few things" and Nuvrail is one of several options they're evaluating.

**Response:** Flag for follow-up. Send them the auto-approval rules explainer. If they don't have an active AI email agent, offer to help them connect one. If they're evaluating alternatives, ask directly what the alternatives are.

---

### 🟡 Upgrade Candidate Signals

These indicate a user who is getting value and might be approaching a ceiling:

- Multiple people on their team need to see approval queues. (Team plan is the answer.)
- They're connecting more than one inbox or more than one agent. (Multi-account Pro is the answer.)
- They want a longer audit log than 30 days. (Pro gives them 90 days.)
- They asked about API access or webhooks. (Power user — put them in touch with KC.)
- They mentioned a compliance or legal requirement. (Audit log as compliance artifact — this is a paid feature story.)
- They said "I'd pay for X" where X is already in Pro. Close gently.

**Response:** Send them the Pro feature comparison within 24 hours. Keep it one paragraph. Don't make them scroll.

---

### 🟢 Word-of-Mouth Candidate Signals

These indicate a user who is likely to tell someone else:

- They already described it to a colleague or mentioned it in passing on the call. ("I was telling someone at work about this the other day…")
- They made an analogy that wasn't one of ours. ("It's like version control for email" from their own mouth is pure gold.)
- They got visibly emotional about the "aha" moment — catching an action they would have missed. Strong retellers of this story.
- They pushed back on our positioning with something sharper. ("I think the real value is X, not Y.") People who improve our narrative tell it more.
- They asked if there's a referral program or community.

**Response:** Invite them to the feedback channel with a personal note. Ask if they'd be willing to share their experience publicly (blog post, quote, tweet). Don't ask for a formal testimonial on the first call — let the relationship develop for a week first.

---

*Update this guide as patterns emerge across the first 10 calls. The patterns you find in calls 3–7 will sharpen every call after that.*

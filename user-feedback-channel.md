# User Feedback Channel — Setup & Operations Guide

**Channel name:** #nuvrail-feedback (Slack) / nuvrail-feedback (Discord)  
**Who sends the invite:** CRO (Stella) or whoever ran the onboarding call  
**When:** Within 24 hours of the onboarding call  
**Audience:** First 25 users, added after their onboarding call

---

## Part 1: Invite & Welcome Messages

---

### Slack DM — Sent to Each Person Before Adding Them

*(Sent individually. Personalize the first line. Do not use a template header.)*

---

> Hi [name] — great talking earlier.
>
> I wanted to invite you to a small Slack space we're running for our first users. It's called #nuvrail-feedback, and it's exactly what it sounds like: a place where you can tell us what's broken, what's confusing, or what you wish existed.
>
> A few things I want to be clear about before you join:
>
> **This is not a marketing channel.** We won't be broadcasting announcements or asking you to share things. We'll be asking you specific questions about your experience, and we want specific answers.
>
> **We read everything.** The channel is managed by me and [Jack/KC]. When something comes in, one of us responds — usually within a few hours during weekdays, within 24 hours on weekends.
>
> **Your feedback changes the product.** We're pre-v1 in a meaningful sense. What you say in the next 30 days will directly shape what we build next.
>
> **One concrete ask for your first week:** Think of one moment where using Nuvrail felt like friction — where you had to stop and think, or where the product made you do something extra. Drop that in the channel. That's it. One moment.
>
> I'll add you now if you're up for it. Just reply "yes" and I'll send the invite.

---

### Channel Welcome Message — Pinned at Top of #nuvrail-feedback

*(This is the first thing someone sees when they join. Keep it short and honest.)*

---

> **Welcome to #nuvrail-feedback.**
>
> You're here because you're one of the first people to actually use Nuvrail. We want to know what that's like — the good parts, the bad parts, and the parts where you weren't sure what you were supposed to do.
>
> **How this channel works:**
>
> — We'll post a specific question here roughly once a week. Something concrete, not "how's it going?"
>
> — You can also post anything unprompted: bugs, confusion, feature ideas, or things you showed someone else and they asked about.
>
> — Someone from the team (Stella, Jack, or KC) responds to everything. Not with a canned reply — with an actual answer.
>
> — Every week we share a short note about what we heard and what we did with it.
>
> **What we need from you this first week:**
>
> Tell us about one moment where using Nuvrail felt like friction. Doesn't have to be a big thing. Something that made you pause or do an extra step you weren't expecting. Just drop it as a message here.
>
> **What we'll do with it:**
>
> If it's a bug, we'll log it and tell you. If it's a design issue, we'll put it in the queue. If we're not going to fix it (yet), we'll tell you that too and explain why. We'd rather be honest than look like we have it more together than we do.
>
> Glad you're here. This is the part of building a company that actually matters.
>
> — Stella

---

## Part 2: Community Setup Guide (Internal)

**Who this is for:** Whoever manages the channel (CRO by default; can be delegated after the first 30 days).

---

### What to Post and When

The channel should feel like a two-way conversation, not a bulletin board. That means we post things that invite responses, not things that perform activity.

**Suggested cadence:**

| Frequency | What to post |
|-----------|-------------|
| Once per week | A specific, focused question about a feature or experience. Not "what do you think?" but "Last week we shipped auto-approval rules. If you've tried them: what made you trust or not trust the first rule you set up?" |
| Once per week | A "what we heard / what we did" update. Short. Three to five sentences. Specific. "Three people mentioned the approval queue felt slow on long email threads. We identified the issue — it's a rendering problem on threads over 50 messages. Fix is in review, should be out by [date]." |
| As needed | Bug acknowledgments. When someone reports something in the channel, confirm receipt publicly, even if you already replied in thread. It signals to everyone that reporting works. |
| Monthly | A digest message (see Monthly Digest section below). |

**What not to post:**
- Fundraising announcements (people feel used)
- Marketing content asking them to share or retweet
- Questions so broad they produce useless answers ("what features would you like?")

---

### How to Handle a Complaint or Bug Report

When a user drops a bug or complaint in the channel, do the following in order:

**1. Acknowledge within 4 business hours.**

Even if you don't have an answer yet. A simple "Got this — I'm looking into it" is better than silence.

> "Thanks for flagging. I'm going to pull the logs and understand what happened. I'll come back to you with more context by end of day."

**2. Investigate before responding with a cause.**

Don't speculate publicly about root cause. If you're not sure, say so.

**3. Respond with a real update — not a ticket number.**

Users in a feedback channel are not a support portal. Tell them what you found, what you're going to do, and when.

> "Found it. When you connected a Gmail account with 2FA enabled and a third-party app password, there's a race condition in our IMAP handshake that causes the queue not to populate. It's a real bug. We've filed it and it's going into the next sprint. In the meantime, here's a workaround: [workaround]. Sorry for the friction."

**4. Close the loop publicly.**

When the fix ships, post in the thread: "This is fixed in [version / today's deploy]. Let us know if you hit it again."

**5. Thank them.**

A bug reported by a paying (or free-tier) user in a feedback channel is a gift. Treat it like one.

---

### What Counts as an "Active Feedback User"

For KPI purposes, a user is **active** in the feedback channel if they:

- Respond to at least **2 team-posted questions** within a **30-day window**, OR
- Post at least **1 unprompted message** (bug, idea, observation) in a 30-day window

A user who was active last month but hasn't responded to anything in 30 days is **lapsed**. Follow up with them directly — DM, not a channel ping.

Track this in a simple spreadsheet. Don't over-engineer it. You need: name, join date, last response date, total responses.

---

### How to Identify a Word-of-Mouth Candidate

A user is a word-of-mouth candidate if you observe at least two of the following:

1. **They talk about the product unprompted in the channel.** Not in response to a question — they just post something because they were thinking about it.

2. **They've already shared it.** They mention showing it to a colleague, posting about it, or explaining it to someone. ("I was explaining Nuvrail to my CTO last week and she asked…")

3. **They defend it when a skeptic appears.** If another user expresses doubt and this person responds with a concrete counterargument — especially one that's not in our marketing copy — they're an advocate.

4. **They use language we didn't give them.** They have their own way of explaining what Nuvrail does, and it's better (or at least different from) our version. People who have their own words for a product are people who talk about it.

5. **They ask if there's a referral program or community.** This is the clearest signal. Don't ignore it.

**What to do when you identify one:**

- Send them a personal DM (not a template). Tell them specifically what you noticed. ("I saw you explained the audit log to [other user] the other day and the way you framed it was really clear — do you mind if I quote you?")
- Ask if they'd be willing to share their experience more publicly: a quote, a short tweet, a paragraph for our site, or a conversation with a journalist.
- Don't ask for this before they've been in the channel for at least a week. Let the relationship develop first.

---

### Monthly Digest — What to Capture and Report to the Board

At the end of each month, pull the following and write a short summary (1–2 pages max):

**Quantitative:**
- Total active feedback users (per definition above)
- Total messages in channel (posts + replies)
- Number of bugs reported
- Number of bugs resolved and closed in same month
- Number of feature requests
- Number of users who have lapsed (were active, now aren't)
- Number of users identified as word-of-mouth candidates

**Qualitative (the useful part):**
- The 3–5 most common themes in what users complained about or asked for
- The 2–3 most positive things users said — in their own words, not paraphrased
- Any user quotes that could become testimonials or marketing copy (with permission)
- Any product insight that changed or should change something we're building
- Any user who churned — and what they said before they churned (this is often more valuable than anything else)

**The one question to answer in every digest:**

> *If we only fixed one thing based on what users told us this month, what should it be?*

Answer that question explicitly. Don't hedge. The board should be able to read the digest in five minutes and know whether the product is getting better.

---

*This guide is a living document. Update it after the first month once you know what actually happens in the channel.*

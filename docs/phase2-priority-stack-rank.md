# Phase 2 Priority Stack Rank — Post-v1 First 30 Days

_Authored by KC (CPTO), 2026-05-05. For board alignment before Phase 2 development begins._

---

## Stack Rank

| Rank | Item | Rationale | Estimate |
|------|------|-----------|----------|
| 1 | **Gmail XOAUTH2 smoke test** (issue #59) | Gates the only real user account. All code is written; blocked on Martin completing GCP OAuth2 console setup (issue #53 — in progress). First thing to close once the token is valid. | 1–2 days once unblocked |
| 2 | **Rules engine — backend** (issue #41) | Single highest-leverage feature for the Phase 0 cohort: lets Martin auto-approve low-risk operations without manual review. Reduces approval fatigue, which is the #1 friction point for daily use. No external dependencies. | 1 week |
| 3 | **Rules engine — UI** (issue #42) | Required companion to #41. Rules have no value without a surface to create and manage them. | 3–4 days |
| 4 | **Per-agent audit filter UI** (issue #44) | Martin needs to be able to see what a specific AI agent has done over time, not just a flat log. Currently the audit view is global. Low implementation cost; high diagnostic value. | 2–3 days |
| 5 | **Undo operation** (issue #43) | Implemented as of this cycle (commit 9688916). Route live at `POST /api/v1/operations/{id}/undo`. Pending UI surface in Phase 2. | UI: 1–2 days |
| 6 | **Connection validation on agent creation** (issue #46) | Currently skipped for OAuth2 agents. A bad refresh token silently fails on first proxy use. Worth adding a token-exchange validation step at agent-create time for OAuth2. | 1 day |
| 7 | **Apple iCloud app-specific password setup** (issue #39) | Third email provider after Gmail. Needed before a second user with an iCloud account can onboard. Lower urgency until we have a second user. | 2–3 days |
| 8 | **Outlook / Azure AD OAuth2** (issue #40) | Fourth provider. Same logic as #39 — lower urgency until there's a concrete user who needs it. | 1 week |
| 9 | **Operation batching engine** (issue #45) | In-memory implementation shipped (commit 9688916). DB-backed version for Phase 3. Batching UI (grouping approval cards) is the remaining Phase 2 work. | UI: 3–4 days |

---

## Scoring criteria applied

1. **User impact to Phase 0 cohort (Martin)** — does this reduce friction or add capability for the only real user right now?
2. **Risk reduction for the proxy** — does this close a gap that could cause silent failures or data loss?
3. **Prerequisite dependency order** — does this unblock other items?

---

## What was explicitly deprioritised and why

- **iOS native app** — Decision fork open (fork 2ebcb742). Recommendation is Phase 3; board to resolve.
- **Postgres migration** — Decision fork open (fork 0b17c881). Recommendation is Phase 3 with documented migration plan; board to resolve.
- **Stripe / billing** — Phase 3. No second user yet.
- **Multi-tenant onboarding** — Phase 3. Premature before proving Phase 0 cohort.
- **Developer API** — Phase 3.

---

## Note on XOAUTH2 / Gmail status

The XOAUTH2 implementation is complete (#55–#58, commit c145eed). The blocker is issue #53 (Martin: GCP console OAuth2 client creation), which is now partially done — the OAuth2 client exists and keys are in `.env`. Current failure is `invalid_grant`, most likely caused by the Gmail API not being enabled in the GCP project. Once resolved, #59 (smoke test) closes immediately.

This is the **single highest-risk item for the v1 launch deadline (2026-05-19)**. Every day of delay narrows the testing window.

---

_This document is the artifact for Ripple Path Technical Roadmap step 0edd2a20. Present at next board standup for alignment before Phase 2 development begins._

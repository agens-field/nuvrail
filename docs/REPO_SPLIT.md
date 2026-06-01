# Splitting Nuvrail into Public Core + Private Enterprise

**Status:** Proposal / planning
**Last updated:** 2026-06-01

This document describes how to split the current single Nuvrail repository into:

- **`nuvrail`** — a public, open-source core (the free tier), and
- **`nuvrail-enterprise`** — a private repository containing paid features, deploy
  tooling, and internal helpers.

It is grounded in the current code layout. Where a concrete change is proposed,
the relevant file (and line, where stable) is cited.

---

## 1. Two independent dimensions (read this first)

The split conflates two things that must be kept separate in the design:

1. **Source availability** — *does the code for a feature physically exist in
   this checkout?* Determined by which repo a file lives in. A self-hoster of the
   public repo simply does not have the enterprise code.
2. **Runtime entitlement** — *in a deployment that DOES have the code (our hosted
   SaaS), may THIS user use the feature?* Determined at runtime by the user's plan
   (free / pro / enterprise), driven by billing.

The proposed pricing maps onto **both** dimensions:

| Feature | Source dimension | Runtime entitlement |
|---|---|---|
| Auto-approval rules **engine** | Lives only in **enterprise** repo | Gated to the `enterprise` plan |
| **Multi-agent** (> 1 agent) | Limit *policy* lives in **enterprise** (it is billing policy, not a code feature) | `free` = 1, `pro` = N, `enterprise` = unlimited |

**Key consequence:** the agent limit is **not** hard-coded as "max 1 agent" in
core. Baking a pricing decision into the open product is both hostile to OSS
self-hosters (they have the source) and wrong layering. Instead, **core exposes a
seam** and the **policy lives in the enterprise entitlements provider**. Public
core defaults to "unlimited; only what is installed." Our SaaS runs the
enterprise build and enforces the plan limits.

---

## 2. Current coupling (what the code looks like today)

A survey of the repo shows the rules engine is **almost cleanly separable**:

- Self-contained units: `gateway/rules.py` (matching + evaluation),
  `api/routes/rules.py` (CRUD + `/rules/test`), `web/src/views/RulesView.tsx`,
  the rules Pydantic models in `api/models.py`, and `tests/**/test_rules.py`.
- The `auto_approval_rules` table is defined in `gateway/state_db.py`.
- **The one hard coupling:** `gateway/staging.py` (~line 232) calls
  `evaluate_rules()` *inline* in the operation-creation path, then applies the
  decision via `_apply_auto_rule_decision()`. This is the single seam that
  everything hinges on.
- `staged_operations.decided_by` and the `audit_log.actor` columns store the
  string `'auto_rule'`. These are **just data** — no schema coupling — and stay
  in core untouched.
- Auto-approved operations execute through the **shared**
  `gateway/execution.py::execute_operation()`. Clean function boundary; no
  rules-specific logic there.

Other relevant facts:

- **No agent-count limit exists today.** `POST /agents`
  (`api/routes/auth.py`) inserts an `agent_credentials` row with no quota check.
- **No plan / tier / subscription / billing / feature-flag mechanism exists.**
  The `users` table (`gateway/state_db.py`) has only
  `id, email, display_name, hashed_password, api_token, created_at`.
- The web frontend (`web/`) is a **separate React SPA** (Vite + Tailwind +
  TanStack Query), built and served by nginx as its own Docker service — it is
  **not** mounted by FastAPI.
- The FastAPI app (`api/main.py`) registers 8 routers; the proxies launch from
  `docker-entrypoint.sh` as `python -m gateway.proxy` / `gateway.smtp_proxy`
  alongside `uvicorn api.main:app`.

---

## 3. Recommended topology: open-core with a plugin overlay

```
nuvrail            (public)   ->  pip-installable package `nuvrail`
                                  proxies, API, base web, free-tier behaviour
nuvrail-enterprise (private)  ->  depends on `nuvrail`; ships a plugin package
                                  (rules engine, entitlements, billing) + deploy/fly
```

- **Public** is fully runnable on its own = the free tier.
- **Enterprise** installs `nuvrail` + `nuvrail-enterprise`, and the enterprise
  package **registers itself into core** through the three extension points in
  §4. The combined image is what we deploy.
- **No forking.** Core never imports enterprise; enterprise plugs into core. This
  keeps the public repo free of enterprise concepts and keeps upgrades painless.

Rejected alternative: a "private overlay repo that just copies/patches files."
It works initially but drifts and forces merge conflicts on every core change.
The plugin model is worth the small upfront refactor.

---

## 4. The three extension seams to add to core

These small, **behavior-preserving** refactors are the real work. All three
no-op in the public repo (they do nothing without a plugin registered), so they
are safe to land first while the app keeps working and the test suite stays
green.

### Seam 1 — Auto-decision hook (fixes the one hard coupling)

New `nuvrail/extensions.py` in core:

```python
# core
_auto_decision_provider = None

def register_auto_decision_provider(fn):
    global _auto_decision_provider
    _auto_decision_provider = fn

async def run_auto_decision(op, *, db_path, user_id):
    """Return 'approve' | 'reject' | None. None when no provider is registered."""
    if _auto_decision_provider is None:
        return None
    return await _auto_decision_provider(op, db_path=db_path, user_id=user_id)
```

`gateway/staging.py` (~line 232) changes from `evaluate_rules(...)` to
`run_auto_decision(...)`. With no plugin, it returns `None` and staging behaves
exactly as a manual-approval system.

`gateway/rules.py` and the rules-specific parts of `_apply_auto_rule_decision()`
move to enterprise. The **generic** decision-application (set status, write the
audit `actor`, call `execute_operation()`) stays in core and is reused by the
plugin so that manual and rules approvals execute identically.

### Seam 2 — Plugin registration (routers + migrations + hooks)

Auto-discover installed plugins via `importlib.metadata` entry points:

```python
# core; called from api/main.py lifespan AND from each proxy's startup
import importlib.metadata

def load_plugins(app=None):
    for ep in importlib.metadata.entry_points(group="nuvrail.plugins"):
        ep.load()(app)   # plugin's setup(app) registers routers/migrations/hooks
```

Core also gains a small **migration-registration hook** so a plugin can create
its own tables at `init_db()` time (`auto_approval_rules`, `users.plan_tier`,
billing tables). Those table definitions leave `gateway/state_db.py` and move
into the enterprise plugin.

The enterprise package's `setup(app)`:

```python
def setup(app):
    register_auto_decision_provider(rules_engine.evaluate)
    register_migration(enterprise_schema)          # auto_approval_rules, plan_tier, billing
    register_entitlements(PlanEntitlements())
    if app is not None:                             # API process only
        app.include_router(rules_router, prefix="/api/v1")
        app.include_router(billing_router, prefix="/api/v1")
```

### Seam 3 — Entitlements interface (the pricing enforcement point)

New `nuvrail/entitlements.py` in core:

```python
from typing import Protocol

class Entitlements(Protocol):
    async def assert_can_create_agent(self, user, current_count: int) -> None: ...
    async def feature_enabled(self, user, feature: str) -> bool: ...

class OpenCoreEntitlements:           # public default: nothing is gated
    async def assert_can_create_agent(self, user, current_count): return
    async def feature_enabled(self, user, feature): return True

_active = OpenCoreEntitlements()
def register_entitlements(impl): 
    global _active
    _active = impl
def entitlements(): 
    return _active
```

The **only** core change to `POST /agents` (`api/routes/auth.py`, before the
insert):

```python
async with get_db(db_path) as db:
    (count,) = await (await db.execute(
        "SELECT COUNT(*) FROM agent_credentials WHERE user_id=? AND revoked_at IS NULL",
        (uid,))).fetchone()
await entitlements().assert_can_create_agent(current_user, count)   # raises 402 in enterprise
```

The enterprise `PlanEntitlements` reads `users.plan_tier` and enforces
`free = 1 / pro = 5 / enterprise = unlimited`, raising `402 Payment Required`.
Public core's default never raises.

A `GET /api/v1/features` endpoint (core, backed by `entitlements()`) lets the web
app show/hide gated UI and render upgrade prompts.

---

## 5. What physically moves where

### Stays public (`nuvrail`)

- `gateway/` proxies, `staging.py` (now with the hook), `execution.py`,
  `state_db.py` (base tables only), `audit.py`, `secret_store.py` /
  `credentials.py` (backend-agnostic; no secrets), `oauth2_tokens.py`, parsers,
  security controls.
- `api/` core routers: auth/agents, operations, audit, account, push, oauth2,
  health.
- New core seams: `extensions.py`, `entitlements.py`, and the `/features`
  endpoint.
- `web/` base SPA (login, agents, operations, audit, account) + a generic
  "extension slot" for overlay routes.
- A **generic** `Dockerfile` + `docker-compose.yml` for self-hosting.
- Tests for everything public.

### Moves to private (`nuvrail-enterprise`)

- **Rules:** `gateway/rules.py` logic, `api/routes/rules.py`, rules models,
  `tests/**/test_rules.py`, and the `auto_approval_rules` migration.
- **Entitlements / billing:** `PlanEntitlements`, the `users.plan_tier` /
  subscription migration, future Stripe (or manual-admin) webhook router, tests.
- **Web overlay:** `RulesView.tsx` + a billing/upgrade view, built as a superset
  bundle (public web as an npm dependency / workspace base; enterprise adds the
  routes).
- **All deploy / ops:** `fly.gateway.toml`, `fly.staging-gateway.toml`,
  `web/fly.toml`, `web/fly.staging-web.toml`, the GCP key-decode entrypoint shim,
  the production `docker-entrypoint.sh` specifics.
- **Internal CI:** `.github/workflows/deploy.yml`, `e2e.yml`,
  `scripts/report_ci_kpis.py` and the `RIPPLEPATH_API_TOKEN` secret (internal KPI
  reporting — must not be public). `ci.yml` is genericized and split: public runs
  lint + tests; enterprise runs the combined build + deploy.

---

## 6. Pricing -> enforcement mapping

| Plan | Agents | Auto-approval rules | How enforced |
|---|---|---|---|
| Free | 1 | No | `PlanEntitlements.assert_can_create_agent` raises 402 at agent #2; rules plugin gates feature |
| Pro | Many (e.g. 5) | No | Higher agent limit in entitlements provider |
| Enterprise | Unlimited | Yes | No agent limit; `feature_enabled("auto_approval_rules")` true |

Notes:

- The exact numbers live in **one place** — the enterprise `PlanEntitlements`
  implementation — so pricing changes never touch core.
- Public OSS (no enterprise plugin) = effectively unlimited agents, no rules
  engine present. That is the intended free, self-hostable product.

---

## 7. Licensing the public repo (decision required)

This determines whether a competitor can take the public core and run a rival
hosted service.

- **Apache-2.0 / MIT** — maximally adoptable; anyone (including competitors) can
  host it. Fine if the moat is the enterprise features + brand + ops.
- **AGPL-3.0** — copyleft including network use: a competitor hosting a modified
  version must open-source their modifications. Deters closed-source rivals; some
  enterprises avoid AGPL dependencies.
- **BSL 1.1** (HashiCorp / Sentry style) — source-available, free to use except
  as a competing hosted service, converts to an OSS license after N years.
  Strongest commercial protection; not OSI "open source."

**Recommendation:** **AGPL-3.0** for the public repo (genuine open source, blocks
closed SaaS clones), with the enterprise package proprietary. If enterprise
customers refuse AGPL dependencies, **BSL** is the pragmatic alternative. Decide
**before** the repo goes public — it is hard to walk back.

---

## 8. Mechanics of performing the split

1. **Land the three seams in the current (single) repo first.** They are no-ops
   without a plugin, so the app keeps working and tests stay green. This de-risks
   everything else.
2. **Carve out enterprise files with history.** Use `git filter-repo` to extract
   the enterprise paths into the new `nuvrail-enterprise` repo (preserves
   blame/history), then delete them from public in a single commit.
3. **Packaging.** Public `pyproject.toml` already builds `nuvrail`. Enterprise
   `pyproject.toml` declares `dependencies = ["nuvrail==<pin>"]` (private index or
   `git+ssh`) and an entry point:
   ```toml
   [project.entry-points."nuvrail.plugins"]
   enterprise = "nuvrail_enterprise:setup"
   ```
4. **Docker.** Enterprise image is
   `FROM python; pip install nuvrail nuvrail-enterprise; COPY deploy entrypoint`.
   Public ships a vanilla self-host image.
5. **Scrub before public.** Remove internal fly app names, the RIPPLEPATH
   reporter, internal URLs; run `gitleaks` over full history (the repo already has
   `.gitleaks.toml`).
6. **Database.** Base tables in core `init_db()`; enterprise tables via registered
   migrations against the same SQLite file. A fresh database makes this trivial.

---

## 9. Suggested sequencing

1. **Phase 0 — seams (safe, in-place).** Add `extensions.py`, `entitlements.py`,
   `/features`; convert `staging.py` to `run_auto_decision`; add the entitlements
   call in `/agents`; verify the full suite stays green. *No behavior change.*
2. **Phase 1 — internal plugin.** Move rules + entitlements into a
   `nuvrail_enterprise` package *inside the same repo* (private dir) and wire the
   entry point. Prove the plugin model end-to-end.
3. **Phase 2 — repo extraction.** `git filter-repo` into `nuvrail-enterprise`,
   delete from public, set up the cross-repo dependency and the two CI pipelines.
4. **Phase 3 — billing.** Add `plan_tier` provisioning + Stripe (or manual admin)
   and the upgrade UI. The entitlements seam is where this lands.
5. **Phase 4 — license + publish.** Apply the chosen license, do a final scrub,
   flip the public repo to public.

---

## 10. Gotchas to budget for

- **Frontend is the messiest split.** React does not "plugin" as cleanly as
  Python. The superset-build approach (public web as a base dependency, enterprise
  overlays routes) works but needs an npm workspace setup. Plan a spike.
- **Keep auto-approve execution shared.** Only the *matching* logic is private;
  the generic decision-application stays in core so manual and rules approvals
  execute identically and never diverge.
- **Audit / data continuity.** `decided_by='auto_rule'` rows render in the public
  audit UI; keep these string values stable so enterprise-decided operations
  display gracefully even in a public-only build.
- **License reversibility.** Lock the license choice before going public.

---

## Appendix: key file references (current repo)

| Concern | File(s) |
|---|---|
| Rules evaluation engine | `gateway/rules.py` |
| Rules CRUD API | `api/routes/rules.py` |
| **Hard coupling seam** | `gateway/staging.py` (~line 232, `evaluate_rules` call) |
| Rules table | `gateway/state_db.py` (`auto_approval_rules`) |
| Rules UI | `web/src/views/RulesView.tsx` |
| Agent creation (limit seam) | `api/routes/auth.py` (`POST /agents`) |
| Users table | `gateway/state_db.py` (`users`) |
| App / router wiring | `api/main.py` |
| Proxy entry points | `gateway/proxy.py`, `gateway/smtp_proxy.py`, `docker-entrypoint.sh` |
| Deploy configs | `fly.gateway.toml`, `fly.staging-gateway.toml`, `web/fly.toml`, `web/fly.staging-web.toml` |
| CI/CD | `.github/workflows/{ci,deploy,e2e}.yml`, `scripts/report_ci_kpis.py` |

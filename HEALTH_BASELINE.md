# Engineering Health Baseline
_Captured: 2026-05-04_

## Test Suite
- **Pass rate:** 323/323 (100%) as of 2026-04-21
- **Total tests:** 323
- **P95 run time:** Not yet measured (no CI; cron sandbox lacks Python venv)
- **Framework:** pytest + pytest-asyncio + httpx

## Deployment
- **Deploy success rate:** 100% (manually confirmed, no recorded failures since 2026-04-13)
- **Production environment:** test.nuvrail.com on server.mmodahl.com (74.208.7.184)
- **Live since:** 2026-04-13
- **Deploy method:** `cd /opt/nuvrail && git pull && docker compose build gateway && docker compose up -d gateway`

## Incidents
- **On-call paging rate:** 0 pages (pre-launch, no incidents recorded)
- **Open P0/critical issues:** 0

## Next Steps
- Wire CI pass rate to automated KPI reporting (step 1dfc63e6 on Engineering Health path)
- Measure and record P95 test run time once CI is configured

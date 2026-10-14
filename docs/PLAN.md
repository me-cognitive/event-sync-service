# Event Sync Service — Implementation Plan

> AI-collaborated planning document (kept in the repo as requested by the assessment brief).
> Stack: **Python 3.12+ + FastAPI** (backend), **Angular 21 + Angular Material** (frontend), **AWS CDK → S3 + CloudFront + EC2/Docker** (deployment).

---

## 1. Problem Summary

Build a service that:

1. **Ingests** meeting records from two upstream sources:
   - **CRM** (`crm_events.json`): business context — client, company, relationship owner, meeting type, notes, deal status.
   - **Calendar** (`calendar_events.json`): scheduling truth — exact start/end, attendee emails, location/link, recurrence.
2. **Reconciles** records referring to the same real-world meeting (no shared ID).
3. **Serves** a unified meeting list via a REST API and an Angular UI that shows **field-level provenance** and **conflicts**.

Non-functional: single-command start, detailed README (setup, approach, key decisions, time spent), AI collaboration docs.

Not evaluated: visual polish, performance, coverage %. **README quality ≈ code quality.**

---

## 2. Data Analysis (all 42 records reviewed)

### 2.1 Expected reconciliation result

| # | CRM | Calendar | Notes / issues |
|---|-----|----------|----------------|
| 1 | CRM-1001 | CAL-A1 | Location "HQ - Conference Room B" vs "Conference Room B" (equivalent). Status Completed vs confirmed (lifecycle difference, not a conflict). |
| 2 | CRM-1002 | CAL-A2 | **Conflict:** In-Person / NYC Office vs Zoom link. |
| 3 | CRM-1003 | — | CRM-only (Lakeshore intro call). Location null. |
| 4 | CRM-1004 | CAL-A4 | **Timezone trap:** CAL is `19:00Z`; on 2025-03-13 New York is on EDT (DST began 2025-03-09) → **15:00 local** vs CRM 14:00. 1h conflict. |
| 5 | CRM-1005 | CAL-A5 + **CAL-A6** | **Intra-source duplicate** in Calendar: same organizer, client, day, overlapping time; A6 is newer (+CIO Sandra Mills, 11:30). |
| 6 | CRM-1006 | CAL-A7 | Internal, recurring. Clean. |
| 7 | CRM-1007 | CAL-A8 | CRM `meeting_time: null` → fill from calendar (15:00). Virtual vs Zoom (compatible). |
| 8 | CRM-1008 | CAL-A9 | **Malformed date** `"03-15/2025"` → lenient parse to 2025-03-15. |
| 9 | CRM-1009 | CAL-A10 | **Conflict:** CRM Cancelled vs Calendar confirmed. Notes contain an em dash (`—`) — see encoding note below. |
| 10 | CRM-1010 | — | CRM-only (Northwind, Tentative, empty notes). |
| 11 | CRM-1011 | CAL-A12 | Location "NYC Office" vs "NYC Office - 12th Floor" (refinement). Subject wording differs. |
| 12 | CRM-1012 | CAL-A13 | Clean. |
| 13 | CRM-1013 | CAL-A14 | Clean (internal). |
| 14 | CRM-1014 | CAL-A15 | Location null in both. |
| 15 | CRM-1015 | CAL-A16 | **Malformed attendee** `raj.patel[at]atlasvc.com`. "HQ - Conference Room A" vs "Conference Room A". |
| 16 | CRM-1016 | CAL-A17 | **Conflict:** 13:00 vs 15:00. |
| 17 | CRM-1017 | CAL-A20 | client_name `"Multiple"`; attendee `"external-guests"` (not an email). "The Palm - DC" vs "The Palm Restaurant". |
| 18 | CRM-1018 | CAL-A21 | CRM location null → fill "Zoom" from calendar. |
| 19 | CRM-1019 | CAL-A22 | Clean. |
| 20 | CRM-1020 | — | CRM-only. |
| 21 | — | CAL-A3, CAL-A18 | Calendar-only. **Two instances of a recurring series — NOT duplicates** (different dates). |
| 22 | — | CAL-A11 | Calendar-only, sparse: no attendees, no location, null description, `end_time` missing seconds. |
| 23 | — | CAL-A19 | Calendar-only. |

→ **24 unified meetings**: 17 matched (one with a 2-record calendar duplicate group), 3 CRM-only, 4 Calendar-only. Verified by the golden test `backend/tests/test_reconcile.py`.

### 2.2 Data quality issue catalogue

| Category | Examples | Handling |
|---|---|---|
| Malformed date | `03-15/2025` | Lenient multi-format parser; record `DATA_QUALITY: normalized_date` issue. |
| Missing time | CRM-1007 | Allow null; fill from matched source; flag if unmatched. |
| Mixed timezone formats | naive vs `Z` suffix | Naive ⇒ firm-local `America/New_York` (configurable); aware ⇒ convert to local. Store both UTC and local. |
| Inconsistent ISO precision | `2025-03-14T20:00` | Tolerant ISO parser. |
| Invalid emails | `[at]`, `external-guests` | Repair `[at]`→`@` (flag as repaired); non-emails kept as "unparsed attendee" + flag. |
| Encoding (platform trap) | CRM-1009 notes `—` | The file is valid UTF-8, but reading it with Windows' default code page (cp1252) yields `â€”`. Initial analysis was misled by exactly this. Loaders always decode bytes as UTF-8; a defensive cp1252→utf-8 repair remains for genuinely mis-encoded upstream text. |
| Placeholder values | `client_name: "Multiple"` | Treat as unknown/group; flag. |
| Nulls / empty strings | location, notes, description | Normalize `""` → null. |

Every issue is attached to the record (and surfaced in the UI). **Nothing is silently dropped.**

---

## 3. Key Design Decisions

### D1. Normalize first, then match
Map both sources into a common `NormalizedEvent` (source, source_id, title, start/end (tz-aware), date, client names, company, owner, attendee emails, external domains, location, location_kind {physical|virtual|unknown}, status, notes, recurrence, created_at, issues[]).

### D2. Explainable scoring-based matching (no ML)
For each (CRM, Calendar) pair on the **same local date** (±1 day for tz edge cases), compute a weighted score:

| Signal | Weight (initial) |
|---|---|
| Same local date | gate (required) |
| Start-time proximity (0 min = full, decays to 0 at ≥3h; null time = neutral) | 0.30 |
| Client match: CRM client name ↔ attendee email local-part (`david.park`) | 0.25 |
| Company match: company tokens ↔ title tokens / attendee domain (`meridiancap`) | 0.20 |
| Owner match: relationship_owner ↔ organizer/attendee (`sarah.chen`) | 0.10 |
| Title/subject token similarity (rapidfuzz) | 0.10 |
| Location similarity | 0.05 |

- Threshold ≥ 0.55 → candidate. Resolve one-to-one with **greedy best-score assignment** (with only a handful of same-day candidates it equals the optimal Hungarian result and is easier to explain). Duplicate groups are matched as a unit using their best-scoring member.
- Each match stores `confidence` and `match_reasons[]` (shown in UI) — explainability is the point.
- Weights live in config so they're tweakable and documented.

### D3. Intra-source duplicate detection (run before cross-source matching)
Same source + same date + same organizer + overlapping time window + same external participant/company + high title similarity → **duplicate group**.
- **Recurring events are excluded** (A3/A18 differ by date anyway).
- Canonical = most recently `created_at` (A6, assumed to be the updated version); others kept as `superseded_records` and flagged `POSSIBLE_DUPLICATE`.
- The group as a whole matches to CRM-1005; the time difference vs CRM is shown as a conflict.

### D4. Field-level merge with provenance ("source of truth per field")

| Field | Preferred source | Rationale |
|---|---|---|
| start/end time | Calendar | Scheduling system of record; has end time + explicit tz. |
| attendees | Calendar | Actual invitees. |
| location | Calendar (fallback CRM) | Calendar holds the real room/link. |
| client_name, company, owner | CRM | Relationship system of record. |
| meeting_type | CRM, **validated** against calendar location kind | Mismatch ⇒ conflict. |
| notes/description | Both kept (no conflict — complementary). |
| status | **Most restrictive wins** (Cancelled > Tentative > Confirmed) | Safer to show a cancellation; always flagged as conflict. |
| title | Calendar title, CRM subject retained. |

Each unified field = `{ value, source: "crm"|"calendar"|"both", values_by_source: {crm, calendar}, conflict: bool, resolution_rule }`.

### D5. Conflict vs. "benign difference"
Not every difference is a conflict — classify to avoid noise:
- **Equivalent** (no flag): "HQ - Conference Room B" ≈ "Conference Room B"; "Virtual" ≈ "Zoom".
- **Refinement** (info): "NYC Office" → "NYC Office - 12th Floor"; null → value (gap filled).
- **Conflict** (warning): In-Person vs Zoom; 14:00 vs 15:00; Cancelled vs confirmed.

Severity: `info | warning | critical` (status cancellation & time conflicts = critical).

### D6. Storage: in-memory, rebuilt on sync
Reconciliation is deterministic over ~40 records. Store the result in memory on startup; `POST /api/sync` re-runs the pipeline. A `SourceLoader` interface abstracts file vs S3 vs HTTP so the same pipeline runs locally and on AWS. (A DB is unnecessary for the scope — documented as a future step.)

### D7. Firm timezone assumption
Naive timestamps assumed `America/New_York` (offices: HQ, NYC, DC, Boston). Configurable via env var. Documented clearly in the README as an assumption.

---

## 4. Architecture

```
            ┌───────────────┐    ┌──────────────────────────── FastAPI ─────────────────────────────┐
crm.json ──▶│ SourceLoader  │──▶ │ Normalizer ─▶ DuplicateDetector ─▶ Matcher ─▶ Merger ─▶ Store    │
cal.json ──▶│ (file | S3)   │    │   (+ data-quality issues at every stage)                         │
            └───────────────┘    │                                     REST API  /api/...           │
                                 └──────────────────────────────────────────┬───────────────────────┘
                                                                            │ JSON
                                                              Angular SPA (list, detail, issues)
```

### 4.1 Backend layout
```
backend/
  app/
    main.py                 # FastAPI app, CORS, startup sync
    config.py               # pydantic-settings: data dir/S3, tz, weights, thresholds
    models/
      normalized.py         # NormalizedEvent, DataIssue, IssueCode
      unified.py            # UnifiedMeeting, FieldValue, Conflict, MatchInfo
    ingest/
      loaders.py            # FileLoader, S3Loader (explicit UTF-8 decode)
      normalize.py          # raw dict -> NormalizedEvent per source; bad records kept with an error issue
      parsing.py            # dates, times, tz, emails, mojibake, location kind
      pipeline.py           # ingest(settings) -> list[NormalizedEvent]
    reconcile/
      similarity.py         # token/name/company/location similarity helpers (rapidfuzz)
      duplicates.py         # same-source duplicate groups (union-find), newest = canonical
      scoring.py            # weighted, explainable pair score + reasons
      matcher.py            # greedy one-to-one assignment
      merger.py             # per-field source-of-truth rules, FieldStatus, Conflict
      service.py            # reconcile(events) -> sorted UnifiedMeetings
    api/
      routes_meetings.py
      routes_sync.py
    store.py
  tests/                    # unit tests for parsing, scoring, golden-file test of full result
  pyproject.toml / requirements.txt
  Dockerfile
```

### 4.2 API
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness. |
| GET | `/api/meetings` | Unified list. Query: `source=crm|calendar|both`, `has_conflicts=true`, `status=`, `from=`, `to=`, `q=`. |
| GET | `/api/meetings/{id}` | Full detail incl. field provenance, conflicts, match reasons, raw source records. |
| GET | `/api/issues` | All data-quality issues (by record). |
| GET | `/api/summary` | Counts: total, matched, CRM-only, calendar-only, conflicts, duplicates, issues. |
| POST | `/api/sync` | Re-run ingestion + reconciliation; returns summary. |
| GET | `/docs` | Auto Swagger (FastAPI). |

Unified meeting `id` = deterministic hash of sorted source IDs (stable across syncs).

### 4.3 Frontend (Angular, standalone components, **Angular Material**)
```
frontend/src/app/
  core/api.service.ts, models.ts
  features/
    meetings-list/          # table: date/time, title, client, owner, source badges [CRM][CAL], conflict icon, status
    meeting-detail/         # side-by-side: Field | Unified | CRM | Calendar ; conflicts highlighted; match confidence + reasons; raw JSON toggle
    data-issues/            # list of data-quality issues
  shared/source-badge, conflict-chip
```
- Summary bar at top (matched / CRM-only / Cal-only / conflicts).
- Filters: source, "conflicts only", search.
- Each unified value shows a small source tag; hover shows the other source's value.
- "Re-sync" button → `POST /api/sync`.

---

## 5. Run & Deploy

### 5.1 Local — single command
```
docker compose up --build
```
- `backend` (uvicorn :8000), `frontend` (nginx serving built Angular on :4200, proxying `/api` → backend).
- Data mounted from `./data`.
- Also documented: non-Docker dev mode (`uvicorn` + `ng serve` with proxy config), plus a `Makefile`/script wrapper.

### 5.2 AWS (target deployment) — **Decided: single EC2 + Docker, provisioned with AWS CDK (Python)**

```
 Browser ──▶ CloudFront ──┬── /*      ──▶ S3 (Angular build, private, OAC)
                          └── /api/*  ──▶ EC2 t3.micro (Elastic IP, Docker: FastAPI :80)
                                              │  IAM instance role (read-only)
                                              ▼
                                         S3 data bucket (crm_events.json, calendar_events.json)
```

| Component | Service |
|---|---|
| Source data | **S3** private bucket; files uploaded by CDK `BucketDeployment`; backend reads via `S3Loader` (`DATA_SOURCE=s3`) using the instance IAM role — no keys |
| Backend | **EC2 t3.micro** (**Ubuntu Server 26.04 LTS**, x86_64, free-tier eligible; AMI resolved from Canonical's SSM parameter `/aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id`) running only the backend container; image built locally for `linux/amd64`, pushed to **ECR** (CDK `DockerImageAsset`); user-data installs Docker + AWS CLI, adds 1 GB swap, logs in to ECR, runs the container with `--restart always`. CPU credits = `standard` (no surprise charges); 10 GB gp3 root volume |
| Frontend | **S3 + CloudFront** (Origin Access Control); `/api/*` behaviour → EC2 public DNS custom origin (caching disabled) |
| Networking | Default/simple VPC with public subnet only (no NAT); security group allows port 80 **only from the CloudFront managed prefix list**; SSM Session Manager instead of SSH |
| Logs | Docker `awslogs` driver → CloudWatch |
| IaC | **AWS CDK (Python)** in `infra/`; `cdk deploy` / `cdk destroy` |
| Optional | EventBridge scheduled rule → `POST /api/sync` |

**Decision record — compute choice**

| Option | ~USD/month (us-east-1) | Verdict |
|---|---|---|
| App Runner | — | Not used (no longer supported for new use) |
| A. ECS Fargate + ALB | ≈ $37–40 (ALB + IPv4 dominate) | Production-grade; documented as the scale-up path |
| **B. EC2 + Docker** | **≈ $10–12** (≈$4 when stopped; ≈free on legacy free tier) | **Chosen** — cheapest, same container as local dev |
| C. ECS on EC2, no ALB | ≈ $11–13 | More CDK complexity for little gain |

**Decision record — AWS account & existing resources**
- Account `535635599643`, region **`ap-southeast-2` (Sydney)**; CDK uses a dedicated CLI profile (`hexaware-poc`).
- A manually created EC2 (`m7i-flex.large`, ~$87/mo) and S3 bucket already exist. **Decision: CDK creates everything new** (its own t3.micro, data bucket, web bucket, CloudFront, IAM, SG) and leaves the manual resources untouched, so the whole stack is reproducible and removable with `cdk destroy`.
- Access to the instance is via **SSM Session Manager**; no SSH ingress. Key files in `AWS_details/` are git-ignored and not used by the stack.

**Capacity check — t3.micro (2 burstable vCPU, 1 GiB RAM)**

| Process | Memory |
|---|---|
| Ubuntu 26.04 (incl. snapd) | ~250–300 MB |
| Docker engine | ~80–100 MB |
| SSM agent (preinstalled snap on Ubuntu AMIs) | ~30–50 MB |
| Backend container (measured ~65 MB RSS with all libs; flat over 100 syncs) | ~80–100 MB |
| **Total** | **~450–550 MB** (+1 GB swap as safety net) |

CPU: one full sync of 42 records ≈ 4 ms; idle API ≈ 0% — far below the 10%/vCPU baseline.
Frontend deliberately **not** on EC2: static files are served from S3 + CloudFront (free HTTPS, UI survives instance stop, same-origin `/api` routing, no load on the instance).

Trade-offs accepted for B: single instance (no HA / auto-scaling), manual-ish redeploy (re-run `cdk deploy` replaces instance or SSM command to pull new image). Scale-up path: same image → ECS Fargate behind ALB.

Scope note: local single-command (`docker compose up`, `DATA_SOURCE=file`) is the hard requirement and needs no AWS account; AWS is implemented after core features work.

---

## 6. Repository Layout
```
event-sync-service/
  README.md                 # setup, approach, decisions, assumptions, time spent
  docker-compose.yml
  data/                     # provided JSON files
  backend/
  frontend/
  infra/                    # AWS CDK (Python): data bucket, ECR asset, EC2, CloudFront, web bucket
  docs/
    PLAN.md                 # this file
    AI_COLLABORATION.md     # prompts/brainstorming log, what AI did vs what I decided
    DECISIONS.md            # ADR-style decision log (optional, or in README)
```

---

## 7. Implementation Phases

| Phase | Work | Output |
|---|---|---|
| **0. Scaffold** | Repo structure, FastAPI skeleton, Angular app (`ng new`), docker-compose, git init | Running hello-world end to end |
| **1. Ingest & normalize** | Raw models, lenient parsers (date/time/tz/email/mojibake/location kind), issue capture | Unit tests for every malformed case in §2.2 |
| **2. Reconcile** | Duplicate detection, scoring, matching, merger with provenance + conflict classification | Golden test asserting the table in §2.1 |
| **3. API** | Routes, filters, summary, sync, OpenAPI | Swagger working |
| **4. Frontend** | List, detail (side-by-side provenance), issues, filters, summary bar | Usable UI |
| **5. Docs** | README (run, approach, decisions, assumptions, limitations, future work, time spent), AI collaboration log | Submission-ready docs |
| **6. AWS** | S3 loader, CDK stack (data bucket + ECR image + EC2/Docker + web bucket + CloudFront), deploy/destroy guide, cost notes | Deployed CloudFront URL |

---

## 8. Testing Strategy
- **Unit:** parsers (each malformed input), scoring signals, conflict classifier.
- **Golden test:** full pipeline over provided data → asserts expected pairings, CRM-only/Cal-only sets, the A5/A6 duplicate, A3/A18 not merged, and the listed conflicts.
- **API:** FastAPI `TestClient` smoke tests.
- Frontend: minimal (not evaluated); a couple of component tests if time permits.

---

## 9. Assumptions & Open Questions (to document in README)
1. Naive timestamps = America/New_York.
2. Calendar is the system of record for time/location; CRM for client/relationship data.
3. A cancellation in either source should be shown as cancelled (safety) but flagged.
4. Among calendar duplicates, the newest `created_at` is canonical.
5. Recurring events are treated as independent instances (no RRULE expansion — data has none).
6. CRM statuses like "Completed" vs calendar "confirmed" are lifecycle differences, not conflicts.
7. No authentication (internal tool scope) — noted as future work.

**Future work:** persistent DB (DynamoDB/Postgres) with sync history, manual match override / conflict resolution by user, webhook/incremental ingestion, auth (Cognito).

---

## 10. Time Tracking
Track time per phase here and copy the total into the README.

| Phase | Time |
|---|---|
| Analysis & planning | |
| Backend | |
| Frontend | |
| Docs | |
| AWS | |
| **Total** | |

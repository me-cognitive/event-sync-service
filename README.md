# Event Sync Service

Pulls meeting records from a **CRM** export and a **Calendar** export, works out which records describe the same real-world meeting, and serves one unified meeting list through a REST API and an Angular UI. For every value you can see **which source it came from**, **what each source said**, and **where they disagree**.

| | |
|---|---|
| Backend | Python 3.12 · FastAPI · Pydantic · rapidfuzz |
| Frontend | Angular 21 · Angular Material |
| Cloud | AWS CDK (Python): S3 + CloudFront + EC2 (Ubuntu, Docker) · GitHub Actions via OIDC (no stored AWS keys) |
| Result on the provided data | 42 records → **24 meetings**: 17 matched, 3 CRM-only, 4 Calendar-only · 5 meetings with conflicts · 1 duplicate group · 11 data-quality issues, all repaired or flagged |

---

## Contents

1. [Quick start (one command)](#1-quick-start-one-command)
2. [Running without Docker](#2-running-without-docker)
3. [Using the app](#3-using-the-app)
4. [Approach](#4-approach)
5. [Key decisions](#5-key-decisions)
6. [How each data problem is handled](#6-how-each-data-problem-is-handled)
7. [API reference](#7-api-reference)
8. [Project structure](#8-project-structure)
9. [Testing](#9-testing)
10. [AWS deployment](#10-aws-deployment)
11. [Assumptions, limitations and future work](#11-assumptions-limitations-and-future-work)
12. [AI collaboration](#12-ai-collaboration)
13. [Time spent](#13-time-spent)

---

## 1. Quick start (one command)

**Prerequisite:** Docker Desktop (or Docker Engine + Compose v2) running.

```bash
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:4200 | Web UI |
| http://localhost:4200/api/meetings | API (proxied by the UI's nginx, same origin) |
| http://localhost:8000/docs | Interactive API docs (Swagger) |

The backend reads the JSON files from [`data/`](data/) (mounted read-only) and reconciles them at start-up. Stop with `Ctrl+C`; remove containers with `docker compose down`.

## 2. Running without Docker

**Prerequisites:** Python 3.12+ and Node.js 20.19+ / 22.12+ / 24 (required by Angular 21; developed with Python 3.14 and Node 24).

**Backend** (terminal 1):

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload            # http://localhost:8000
```

**Frontend** (terminal 2):

```bash
cd frontend
npm install
npm start                                # http://localhost:4200, proxies /api to :8000
```

### Configuration

All settings are environment variables (see [`backend/app/config.py`](backend/app/config.py)):

| Variable | Default | Purpose |
|---|---|---|
| `DATA_SOURCE` | `file` | `file` reads `DATA_DIR`; `s3` reads `S3_BUCKET`/`S3_PREFIX` |
| `DATA_DIR` | `<repo>/data` | Folder holding the two JSON files |
| `S3_BUCKET`, `S3_PREFIX` | – | Used when `DATA_SOURCE=s3` (credentials from the IAM role / AWS profile) |
| `FIRM_TIMEZONE` | `America/New_York` | Zone assumed for timestamps that carry no offset |
| `FIRM_EMAIL_DOMAIN` | `firma.com` | Attendees outside this domain are treated as external (clients) |

## 3. Using the app

- **Meetings** — summary cards, then the unified list. Each row shows **CRM** / **Calendar** badges (hover for the source record IDs), a duplicate icon where one source had a duplicate, and a conflict count coloured by severity. Filter by *Matched / CRM only / Calendar only*, toggle *Conflicts only*, or search by title, client, company, owner or record ID. **Re-sync** re-reads both sources.
- **Meeting detail** (click a row) — the core of the requirement:
  - conflicts between sources, in plain language;
  - a **field-by-field table**: unified value (tagged *from CRM* / *from Calendar*), *CRM said*, *Calendar said*, and a status — *Agree, Equivalent, Refined, Filled, Complementary, Conflict, Single source*. Hover the status for the resolution rule;
  - **why the records were matched**: reasons, confidence and a per-signal score breakdown;
  - data-quality issues and the **raw source records**, untouched.
- **Data issues** — every repair or problem found during ingestion, filterable by severity, linked to its meeting.

All times are shown in **firm-local time (America/New_York)** regardless of the viewer's timezone; otherwise a reviewer in another zone would see shifted times and the timezone-related conflicts would make no sense.

## 4. Approach

```
crm_events.json ─┐                                                                   ┌─▶ REST API ─▶ Angular UI
                 ├─▶ 1. Normalize ─▶ 2. Group duplicates ─▶ 3. Match ─▶ 4. Merge ─▶ store
calendar_events ─┘   (repair + log      (within a source)    (score +    (per-field
                       issues)                                assign)     provenance)
```

1. **Normalize** ([`ingest/`](backend/app/ingest/)). Each raw record becomes a `NormalizedEvent` with a common shape: timezone-aware start/end in firm-local time, cleaned emails, external domains, location kind (physical / virtual / unknown), lower-case status. Every repair or gap is recorded as a `DataIssue` (severity *info / warning / error*). **No record is ever dropped**; one that can't be parsed at all is kept with an error issue.
2. **Group duplicates** ([`reconcile/duplicates.py`](backend/app/reconcile/duplicates.py)). Within a single source, two records are the same meeting if they are on the same day, have the same owner/organizer, overlapping times (or starts within 60 min) and the same external party. The **most recently created** record is canonical; the others are kept as *superseded* and shown in the UI.
3. **Match** ([`reconcile/scoring.py`](backend/app/reconcile/scoring.py), [`matcher.py`](backend/app/reconcile/matcher.py)). Only records on the **same date** are compared. A weighted, explainable score:

   | Signal | Weight | Scored as |
   |---|---|---|
   | Start time | 0.30 | 1.0 if equal, decaying linearly to 0 at 3 h apart; 0.5 if CRM has no time |
   | Client | 0.25 | CRM client name ↔ attendee email (`Kevin O'Brien` → `kevin.obrien@…`); 0.7 on surname only |
   | Company | 0.20 | distinctive company words (`Meridian`, not `Capital`) in the calendar title or attendee domain (`meridiancap.com`) |
   | Owner | 0.10 | CRM relationship owner is the calendar organizer/attendee |
   | Title | 0.10 | fuzzy token-set similarity |
   | Location | 0.05 | fuzzy similarity; 0.5 if either is missing |

   Pairs scoring **≥ 0.55** are candidates, accepted greedily from the highest score so each record is used once. On this data, true matches score 0.78–1.00. The closest wrong same-day pair scores 0.46: CRM-1017 (*Fund VII closing dinner*) vs CAL-A19 (*Fund VII roadshow prep*), which share the words "Fund VII" and James Wu but are 5 hours apart. The threshold sits between the two groups; the margin below it is the tightest part of the design and is covered by a test. Each match keeps its reasons and score breakdown; duplicate groups match through their best member, and the API reports which pair was scored.
4. **Merge** ([`reconcile/merger.py`](backend/app/reconcile/merger.py)). Each field gets a value under a fixed source-of-truth policy, both raw values are kept, and the difference is classified (see [decisions D3–D5](#5-key-decisions)).

The result is held in memory and rebuilt on start-up and on `POST /api/sync`.

## 5. Key decisions

**D1. Deterministic, explainable matching, not ML or an LLM.** There is no shared ID and only 42 records. A transparent weighted score lets a user (and a reviewer) see *why* two records were joined, and makes the behaviour testable. The weights live in one dictionary ([`scoring.py`](backend/app/reconcile/scoring.py)).

**D2. Same date is a hard gate.** No meeting in the data legitimately moves across days, and gating removes almost all false candidates. Trade-off: a meeting rescheduled to another day would appear as two meetings (one per source) instead of a conflict.

**D3. Source of truth per field, with nothing overwritten silently.**

| Field | Preferred source | Why |
|---|---|---|
| Start/end, attendees, location, recurrence, title | Calendar | It is the scheduling system: it has end times, explicit offsets, real invitees and join links |
| Client, company, owner, meeting type, notes | CRM | It is the relationship system of record |
| Status | **Most restrictive wins** (cancelled > tentative > confirmed) | Showing a cancelled meeting as live is the costlier mistake. CRM-1009 is *Cancelled* in the CRM but still *confirmed* in the calendar, so it is shown cancelled and flagged as an **error**-level conflict |

**D4. Not every difference is a conflict.** Reporting every textual difference would bury the real problems, so differences are classified:

| Status | Example | Flagged as conflict? |
|---|---|---|
| Equivalent | `HQ - Conference Room B` vs `Conference Room B`; `completed` vs `confirmed` | No |
| Refined | `NYC Office` → `NYC Office - 12th Floor` (more specific value kept) | No |
| Filled | CRM has no time/location; taken from Calendar | No |
| Complementary | different titles; CRM notes vs calendar description | No |
| **Conflict** | In-Person vs Zoom · 14:00 vs 15:00 · Cancelled vs confirmed | **Yes**, with a message |

A physical-vs-virtual mismatch is reported once (on location) rather than twice (location *and* meeting type).

**D5. Conflict messages explain likely causes where the data supports it.**
- CRM-1004 vs CAL-A4: the calendar gives `19:00Z`. On 13 March 2025 New York is already on **EDT** (DST began 9 March), so that is **15:00**, one hour after the CRM's 14:00. The message notes that a 1-hour gap against a UTC timestamp usually means one system ignored daylight saving.
- CRM-1005 vs the CAL-A5/CAL-A6 duplicate pair: the CRM says 11:00, the canonical (newer) calendar record says 11:30, and the superseded record says 11:00. The message says the meeting was likely rescheduled.

**D6. Timezone policy.** Timestamps without an offset are taken as firm-local (`America/New_York`: offices are HQ, NYC, DC and Boston); timestamps with an offset are converted to it. Everything is stored timezone-aware.

**D7. In-memory store, no database.** The whole result is re-derived from the sources in ~4 ms, so persisting it would add moving parts without benefit. A failed re-sync keeps the previous result; the API reports the error in `/api/health` and returns 503 only if nothing was ever loaded.

**D8. Duplicates: newest record wins, nothing is deleted.** CAL-A6 (created 10 Mar, adds the client's CIO, 11:30) supersedes CAL-A5 (created 2 Mar, 11:00). Both stay visible. The two *Weekly Team Sync* entries (CAL-A3, CAL-A18) are recurring instances on different dates and are deliberately **not** treated as duplicates.

**D9. Show times as firm-local in the UI** (see §3).

## 6. How each data problem is handled

| What the brief warned about | Where | Handling |
|---|---|---|
| No common ID | all | Weighted matching on date, time, client, company, owner, title, location (§4) |
| Meetings in one source only | CRM-1003, 1010, 1020 · CAL-A3, A11, A18, A19 | Shown as single-source meetings with the matching badge |
| Timestamps don't match exactly | CRM-1004 (DST), CRM-1016 (2 h), CRM-1005 (30 min) | Matched on other signals; time difference reported as a conflict |
| Conflicting fields | CRM-1002 In-Person vs Zoom · CRM-1009 Cancelled vs confirmed | Conflict with message; resolution rule shown |
| Duplicate within a source | CAL-A5 / CAL-A6 | Grouped; newest canonical; both visible (D8) |
| Malformed date | CRM-1008 `03-15/2025` | Parsed as US M-D-Y → 2025-03-15; *warning* issue |
| Missing time | CRM-1007 `meeting_time: null` | Date kept; start filled from the calendar; *warning* issue |
| Malformed email | CAL-A16 `raj.patel[at]atlasvc.com` | Repaired to `raj.patel@atlasvc.com`; *warning* issue |
| Non-email attendee | CAL-A20 `external-guests` | Kept as an unparsed attendee; *warning* issue |
| Placeholder value | CRM-1017 client `Multiple` | Treated as unknown client; *info* issue |
| Sparse record | CAL-A11 (no attendees, location, description) | Kept as calendar-only; issues logged |
| Mixed timestamp formats | CAL-A4 `…Z`, CAL-A11 `2025-03-14T20:00` | Tolerant ISO parsing; *info* issues |
| Empty strings vs null | e.g. CRM-1010 `notes: ""` | Normalized to null |

**Encoding trap (worth knowing).** The files are valid UTF-8 (CRM-1009's notes contain an em dash, `—`). Opening them with Python's default encoding on Windows (cp1252) turns it into `â€”`. That happened in the initial analysis and was briefly mistaken for corrupt data. The loaders always decode bytes explicitly as UTF-8, and a test guards it. A defensive cp1252-to-UTF-8 repair remains in the parser for genuinely mis-encoded upstream text.

## 7. API reference

Interactive docs: `http://localhost:8000/docs`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | `ready` flag, data source, last sync error |
| GET | `/api/summary` | Counts: records, meetings, matched / single-source, conflicts by field, duplicate groups, issues by severity |
| GET | `/api/meetings` | List. Query: `source=crm\|calendar\|both`, `has_conflicts=true`, `status`, `from`, `to` (YYYY-MM-DD), `q` (text search incl. record IDs) |
| GET | `/api/meetings/{id}` | Full meeting: `fields` (value, source, crm, calendar, status, rule), `conflicts`, `match` (confidence, reasons, breakdown, scored pair), `duplicates`, `issues`, `raw_records` |
| GET | `/api/issues` | Data-quality issues with `meeting_id`. Query: `severity`, `source` |
| POST | `/api/sync` | Re-read both sources and rebuild; returns the summary |

Meeting IDs are a hash of the sorted source record IDs, so they are stable across syncs.

## 8. Project structure

```
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, store.py     # app, settings, in-memory store
│   │   ├── api/routes.py                    # REST endpoints
│   │   ├── ingest/                          # loaders (file/S3), parsers, normalizers
│   │   ├── reconcile/                       # similarity, duplicates, scoring, matcher, merger, service
│   │   └── models/                          # normalized, unified, api response models
│   ├── tests/                               # 67 tests incl. golden end-to-end test
│   └── Dockerfile
├── frontend/
│   ├── src/app/
│   │   ├── core/                            # API service + typed models
│   │   ├── features/                        # meetings-list, meeting-detail, data-issues
│   │   └── shared/                          # source badge, firm-time & display pipes, status metadata
│   ├── Dockerfile, nginx.conf               # production build served by nginx, /api proxied
│   └── proxy.conf.json                      # dev-server proxy
├── infra/
│   ├── app.py, stacks/event_sync_stack.py   # the application stack (see §10)
│   ├── user_data.sh                         # EC2 first-boot: Docker, build, run
│   └── foundation_app.py                    # one-time: OIDC deploy role, CDK execution policy, boundary
├── .github/workflows/deploy.yml             # CI: tests + build on PRs; deploy on main via OIDC
├── data/                                    # the two provided JSON files
├── docs/
│   ├── PLAN.md                              # planning document written before implementation
│   └── AI_COLLABORATION.md                  # how AI was used, decisions, corrections
└── docker-compose.yml
```

## 9. Testing

```bash
cd backend
pytest -q
```

67 tests:

- **Parsers**: every malformed value in the data, DST conversion, email repair, location classification.
- **Normalization** over the real files: each known issue is detected and repaired.
- **Golden reconciliation test** ([`test_reconcile.py`](backend/tests/test_reconcile.py)): asserts the exact pairing of all 42 records, the single-source sets, the duplicate group, that recurring instances are not merged, the exact conflict set per meeting, and stable IDs.
- **Scoring**: true matches above the threshold, same-day non-matches below it.
- **API**: filters, detail payload, issues, re-sync, and behaviour when the data source is unreachable.

Frontend tests were not written; per the brief, coverage is not evaluated, so the effort went into backend correctness. The UI was checked by rendering every page in headless Chrome.

## 10. AWS deployment

**Live:** https://d18s1shaz59kg2.cloudfront.net (API health: [`/api/health`](https://d18s1shaz59kg2.cloudfront.net/api/health), Swagger: [`/docs`](https://d18s1shaz59kg2.cloudfront.net/docs)). Region `ap-southeast-2`; data read from S3.

**How the live instance was deployed.** The PoC account belongs to an AWS Organization whose SCP blocks the GitHub OIDC provider, so the deployment ran from **AWS CloudShell** on a temporary federated session (still no stored keys), following [Governed accounts](#governed-accounts-organization-scps). For speed, CDK was bootstrapped with its **default** execution policy rather than the scoped `EventSyncCdkExecutionPolicy`; the permissions boundary is still applied to every app role. Re-bootstrapping with `--cloudformation-execution-policies` switches to the scoped policy. First deploy: about 7 minutes; the backend then built and started on the instance in about 3 minutes.

```
 Browser ──▶ CloudFront ──┬── /*      ──▶ S3 (Angular build, private, Origin Access Control)
                          └── /api/*  ──▶ EC2 t3.micro · Ubuntu 26.04 · Docker (backend container, :80)
                                              │  IAM instance role (read-only)
                                              ▼
                                         S3 data bucket (data/crm_events.json, data/calendar_events.json)
```

- **Everything is created by AWS CDK (Python)** ([`infra/`](infra/)) in `ap-southeast-2`: `cdk deploy` builds it, `cdk destroy` removes it.
- **Data lives in S3**, uploaded by CDK; the backend runs with `DATA_SOURCE=s3` and reads with the instance's IAM role (no stored keys).
- **The Docker image is built on the Ubuntu instance.** CDK uploads the backend source as a zip; on first boot [`infra/user_data.sh`](infra/user_data.sh) adds swap, installs Docker and the AWS CLI, downloads the source, runs `docker build` and starts the container. Deploying therefore needs **no local Docker and no container registry**. Changing backend code changes the upload, which replaces the instance with a freshly built one (a few minutes of API downtime, acceptable here).
- **Only the API runs on EC2.** The UI is static files on S3 behind CloudFront: free HTTPS, the UI still loads while the instance is stopped, and one origin means no CORS. A CloudFront Function sends Angular routes to `index.html` without masking API 404s.
- **Access via SSM Session Manager**, no SSH port open. The security group accepts HTTP only from CloudFront's managed prefix list. An Elastic IP keeps the origin address stable across stop/start.

### Access model: no long-lived AWS keys

```
 People ──SSO (MFA)──▶ IAM Identity Center ──temporary creds──▶ AWS account
 git push main ──▶ GitHub Actions ──OIDC token──▶ EventSyncGitHubDeployRole ──assume──▶ CDK bootstrap roles ──▶ CloudFormation
                                                  (no secrets in GitHub)                (execution policy: scoped, not Admin)
```

| Who | How they authenticate | What they can do |
|---|---|---|
| People | **IAM Identity Center (SSO)** with MFA; `aws configure sso` caches temporary credentials | Permission sets: *AdministratorAccess* for the one-time setup only, *EventSyncOperator* day to day |
| CI/CD | **GitHub Actions OIDC** → `EventSyncGitHubDeployRole`, trusted only for this repo's `production` environment | Assume the CDK bootstrap roles, nothing else |
| CloudFormation | CDK bootstrap execution role with **`EventSyncCdkExecutionPolicy`** instead of the default AdministratorAccess | Only the services and resource-name prefixes the app uses; may create IAM roles **only** with the permissions boundary |
| App roles (EC2, deployment Lambdas) | Created by the stack | Capped by **`EventSyncPermissionsBoundary`**, which can't be removed |

[`infra/foundation_app.py`](infra/foundation_app.py) creates the OIDC trust, deploy role, execution policy and boundary. It is the only thing an administrator deploys by hand, once. [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs the tests on every PR and push, and deploys on pushes to `main`.

### One-time setup (administrator)

**1. Human access with IAM Identity Center**, so nobody uses the root user or IAM user keys:
1. Console (as root, for the last time) → **IAM Identity Center → Enable**. It offers to create an AWS Organization with this account as the management account; accept (free).
2. **Users → Add user** for yourself, with MFA.
3. **Permission sets**:
   - `AdministratorAccess` (predefined): used only for this one-time setup.
   - `EventSyncOperator` (custom): AWS managed `ReadOnlyAccess` plus an inline policy allowing `sts:AssumeRole` on `arn:aws:iam::<account>:role/cdk-hnb659fds-*` and `ssm:StartSession`. This is enough for `cdk diff/deploy/destroy` and shell sessions on the instance.
4. **AWS accounts** → select the account → assign your user both permission sets.
5. Locally: `aws configure sso --profile hexaware-poc` (SSO start URL from the Identity Center dashboard, region `ap-southeast-2`, choose `EventSyncOperator`). Later sign-ins: `aws sso login --profile hexaware-poc`.

**2. Deploy the foundation and bootstrap CDK.** Sign in through the SSO portal with *AdministratorAccess*, switch to **ap-southeast-2 (Sydney)**, open **CloudShell**, upload `infra/foundation_app.py` (Actions → Upload file), then:

```bash
pip install aws-cdk-lib constructs     # CloudShell Python is a virtualenv: no --user
npx -y aws-cdk@2 deploy --app "python3 foundation_app.py" -c githubRepo=<owner>/<repo> --require-approval never
#   add  -c existingOidcProvider=true  if the account already trusts GitHub's OIDC provider
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
npx -y aws-cdk@2 bootstrap aws://$ACCOUNT/ap-southeast-2 \
  --cloudformation-execution-policies arn:aws:iam::$ACCOUNT:policy/EventSyncCdkExecutionPolicy
```

Note the `DeployRoleArn` output.

**3. Configure GitHub** (repository → Settings):
- **Environments → New environment `production`**: restrict deployment branches to `main`, and optionally add required reviewers as an approval gate.
- **Secrets and variables → Actions → Variables → New repository variable** `AWS_DEPLOY_ROLE_ARN` = the `DeployRoleArn` output. It is a role ARN, not a secret.

### Governed accounts (organization SCPs)

In a company-managed AWS Organization, service control policies can forbid parts of this setup. The PoC account used here, for example, denies `iam:CreateOpenIDConnectProvider` (and offers no IAM Identity Center or IAM console users); people sign in through the platform's federated `AccountFullAccessRole`. SCPs override any permission inside the account, so the setup adapts instead:

1. Check whether the platform already provides GitHub's OIDC provider: `aws iam list-open-id-connect-providers`. If `token.actions.githubusercontent.com` is listed, deploy the foundation with `-c existingOidcProvider=true` and keep the GitHub Actions flow.
2. Otherwise deploy the foundation **without** the GitHub part (it still creates the scoped execution policy and the permissions boundary), bootstrap, and **deploy from CloudShell**, which runs on the user's temporary federated session, so there are still no stored keys:

   ```bash
   npx -y aws-cdk@2 deploy --app "python3 foundation_app.py" -c githubOidc=false --require-approval never
   ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
   npx -y aws-cdk@2 bootstrap aws://$ACCOUNT/ap-southeast-2 \
     --cloudformation-execution-policies arn:aws:iam::$ACCOUNT:policy/EventSyncCdkExecutionPolicy

   cd /tmp && git clone https://github.com/me-cognitive/event-sync-service && cd event-sync-service
   (cd frontend && npm ci && npx ng build)
   cd infra && pip install -r requirements.txt
   npx -y aws-cdk@2 deploy EventSyncStack --require-approval never
   ```

   GitHub Actions keeps running tests and the build on every push; its deploy job stays skipped until `AWS_DEPLOY_ROLE_ARN` is set. To move deployments to GitHub later, ask the platform team to create the GitHub OIDC provider (or grant an SCP exception), then redeploy the foundation with `-c githubRepo=<owner>/<repo>` (plus `-c existingOidcProvider=true` if they created it).

### Deploy

Push to `main` (or run the workflow manually: Actions → *CI / Deploy* → Run workflow). The job tests, builds the UI, assumes the deploy role through OIDC and runs `cdk deploy`; the job summary shows the app URL. A deploy takes about 5–10 minutes (CloudFront is the slow part). After the instance starts, the backend needs 2–4 more minutes to install Docker and build the image; until then `/api/*` returns 502/504.

**From a laptop** (e.g. to try a change before merging), with the SSO profile:

```bash
aws sso login --profile hexaware-poc
cd frontend && npm ci && npx ng build && cd ..
cd infra && python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
npx aws-cdk@2 deploy EventSyncStack --profile hexaware-poc
```

**Troubleshooting:** open a shell with the printed `ShellCommand` (`aws ssm start-session … --profile hexaware-poc`, needs the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)), then `sudo tail -n 100 /var/log/event-sync-bootstrap.log` and `sudo docker logs backend`. Container logs also go to the CloudWatch log group in `BackendLogGroup`. If CloudFormation reports `AccessDenied` for an action, that action is missing from the scoped `EventSyncCdkExecutionPolicy` in [`foundation_app.py`](infra/foundation_app.py): add it and redeploy the foundation.

**Ubuntu release:** the AMI comes from Canonical's public SSM parameter for Ubuntu 26.04. To check it exists in the region, or to fall back to 24.04:

```bash
aws ssm get-parameter --profile hexaware-poc --region ap-southeast-2 \
  --name /aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id
npx aws-cdk@2 deploy EventSyncStack --profile hexaware-poc -c ubuntuRelease=24.04
```

To make it permanent for CI, change `ubuntuRelease` in [`infra/cdk.json`](infra/cdk.json).

**Tear down:** `npx aws-cdk@2 destroy EventSyncStack --profile hexaware-poc` removes the app, including both buckets. The foundation stays for future deploys; remove it from CloudShell as an administrator with `npx aws-cdk@2 destroy --app "python3 foundation_app.py" -c githubRepo=<owner>/<repo>`.

**Why a single EC2 instance?** Options compared (us-east-1 on-demand, approximate):

| Option | ≈ USD / month | Notes |
|---|---|---|
| ECS Fargate + Application Load Balancer | 37–40 | Production-grade; the ALB and public IPv4 addresses dominate the cost |
| **EC2 t3.micro + Docker** (chosen) | **10–12** | ≈ 4 when stopped; free-tier eligible |
| ECS on EC2, no ALB | 11–13 | Extra complexity for little gain here |

**Capacity:** the backend measured ~65 MB RSS with all libraries loaded, flat across 100 syncs. With Ubuntu, Docker and the SSM agent, about 450–550 MB of the 1 GB is used; a 1 GB swap file is the safety net and CPU credits are set to *standard* so bursts can't create surprise charges.

**Scaling path:** the same container image runs unchanged on ECS Fargate behind an ALB.

## 11. Assumptions, limitations and future work

**Assumptions**
- Naive timestamps are firm-local `America/New_York`.
- An ambiguous non-ISO date with the year last is US month-day-year (`03-15/2025`).
- The newest record in a duplicate group is the current one.
- CRM `completed` / `scheduled` and calendar `confirmed` describe the same live meeting, not a conflict.
- Recurring calendar entries are independent instances; there are no recurrence rules to expand.

**Limitations**
- A meeting moved to a different day will not be matched (D2).
- Matching weights and threshold were calibrated on this dataset only (closest wrong pair 0.46 vs threshold 0.55); on real volumes they would need tuning against labelled pairs.
- Greedy assignment is optimal here, but with many dense same-day candidates an optimal (Hungarian) assignment would be safer.
- No authentication: this is an internal tool.
- The single EC2 instance is not highly available.

**Future work**
- Let users confirm or reject matches and choose the winning value for a conflict, stored as overrides.
- Persist sync history to see when sources started to disagree.
- Incremental or webhook ingestion instead of full reloads.
- Authentication (e.g. Amazon Cognito).
- Frontend tests.

## 12. AI collaboration

Built with an AI coding assistant (Claude Code). The planning document written before any code is in [`docs/PLAN.md`](docs/PLAN.md). [`docs/AI_COLLABORATION.md`](docs/AI_COLLABORATION.md) records how the work was split, the decisions I made, and the mistakes the AI made and how they were caught.

## 13. Time spent

| Phase | Time |
|---|---|
| Analysis & planning | _TBD_ |
| Backend (ingest, reconcile, API, tests) | _TBD_ |
| Frontend | _TBD_ |
| Documentation | _TBD_ |
| AWS (CDK, deploy) | _TBD_ |
| **Total** | _TBD_ |

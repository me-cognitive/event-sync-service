# Event Sync Service: Application Guide

A complete reference to how the application works: what it does, every term it uses, the rules it applies, its API and UI, and how it is built, deployed and operated. For setup instructions see the [README](../README.md); for the original plan see [PLAN.md](PLAN.md).

**Live:** https://d18s1shaz59kg2.cloudfront.net · **API docs:** https://d18s1shaz59kg2.cloudfront.net/docs · **Code:** https://github.com/me-cognitive/event-sync-service

---

## Contents

1. [What the application does](#1-what-the-application-does)
2. [Glossary](#2-glossary)
3. [The input data](#3-the-input-data)
4. [Architecture](#4-architecture)
5. [The reconciliation pipeline](#5-the-reconciliation-pipeline)
6. [Data-quality issues](#6-data-quality-issues)
7. [Conflicts](#7-conflicts)
8. [Results on the provided data](#8-results-on-the-provided-data)
9. [Data model](#9-data-model)
10. [REST API](#10-rest-api)
11. [User interface](#11-user-interface)
12. [Configuration](#12-configuration)
13. [Running locally](#13-running-locally)
14. [Testing](#14-testing)
15. [AWS infrastructure](#15-aws-infrastructure)
16. [Security and access model](#16-security-and-access-model)
17. [CI/CD pipeline](#17-cicd-pipeline)
18. [Operations](#18-operations)
19. [Cost](#19-cost)
20. [Design decisions in brief](#20-design-decisions-in-brief)
21. [Limitations and future work](#21-limitations-and-future-work)
22. [FAQ](#22-faq)

---

## 1. What the application does

A sales team's meetings are recorded in two systems that don't know about each other:

- **CRM**: who the client is, which company, who owns the relationship, meeting type, notes, deal status.
- **Calendar**: when exactly the meeting is, who is invited, where (room or video link), whether it repeats.

The **Event Sync Service**:

1. **Ingests** both exports (`crm_events.json`, `calendar_events.json`).
2. **Cleans** them, repairing malformed values and logging every repair.
3. **Reconciles** them: works out which CRM record and which Calendar record describe the same real meeting, even though they share no ID.
4. **Merges** each matched pair into one **unified meeting**, choosing each field's value by a fixed rule and keeping what both sources said.
5. **Flags** where the sources disagree (**conflicts**) and where a record itself is broken (**issues**).
6. **Serves** the result through a REST API and an Angular web UI.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Source** | One upstream system: `crm` or `calendar`. |
| **Source record** | One raw entry in a source file, identified by its **source ID** (`CRM-1001`, `CAL-A1`). |
| **Normalized event** | A source record converted to a common shape: timezone-aware times, cleaned emails, location kind and so on. Both sources become this same type, so they can be compared. |
| **Unified meeting** | The application's output: one real-world meeting, built from one or more source records. |
| **Matched meeting** | A unified meeting built from records in **both** sources. |
| **Single-source meeting** | A unified meeting found in only one source: *CRM-only* or *Calendar-only*. |
| **Reconciliation** | The whole process of deciding which records belong together and merging them. |
| **Duplicate group** | Two or more records from the **same** source that describe the same meeting. |
| **Canonical record** | The record in a duplicate group whose values are used: the **newest** by `created_at`. |
| **Superseded record** | Any other record in a duplicate group. Kept and shown, not used for values. |
| **Match score / confidence** | A number from 0 to 1 saying how likely a CRM record and a Calendar record are the same meeting. |
| **Signal** | One ingredient of the score (time, client, company, owner, title, location), each with a **weight**. |
| **Threshold** | The minimum score for a pair to be accepted as a match: **0.55**. |
| **Match reasons** | Human-readable explanations of the score ("Client David Park is an attendee …"). |
| **Scored pair** | The two records the match score was computed on. For a duplicate group, this is its best-matching member. |
| **Source of truth** | The source whose value wins for a given field (e.g. Calendar for time, CRM for client). |
| **Provenance** | Where a unified value came from: its source, plus the raw value each source gave. |
| **Field status** | How the two sources relate for one field: *agree, equivalent, refinement, complementary, filled, conflict, single source* (§7). |
| **Conflict** | A real contradiction between the two sources for one field, with a severity and a message. |
| **Data-quality issue** | A problem inside a single record (malformed, missing or repaired value), with a code and severity (§6). |
| **Severity** | How serious an issue or conflict is: `info`, `warning` or `error`. |
| **Resolution rule** | The plain-language rule that chose a field's value ("Calendar is the scheduling system of record"). |
| **Location kind** | `physical`, `virtual` or `unknown`, derived from the location text and CRM meeting type. |
| **External domain** | An attendee email domain that isn't the firm's (`firma.com`), i.e. a client's. |
| **Internal meeting** | A meeting with only firm staff (CRM type `Internal`, or all calendar attendees on `firma.com`). |
| **Firm-local time** | `America/New_York`. All times are stored and shown in this zone. |
| **Sync** | Re-reading both sources and rebuilding all unified meetings. |
| **Meeting ID** | A stable 12-character ID: a hash of the sorted source IDs. |
| **Golden test** | A test asserting the exact expected result on the real data files. |

**Infrastructure terms**

| Term | Meaning |
|---|---|
| **CDK** | AWS Cloud Development Kit: infrastructure written as Python code and deployed through CloudFormation. |
| **Stack** | A unit of deployed infrastructure: `EventSyncStack` (the app) and `EventSyncFoundation` (one-time account setup). |
| **CDK bootstrap** | One-time setup per account and region that creates the roles and bucket CDK deploys with (`CDKToolkit` stack). |
| **Execution policy** | The permissions CloudFormation uses when creating resources. CDK's default is AdministratorAccess; this project defines a scoped one. |
| **Permissions boundary** | A policy capping the most an IAM role can ever do, whatever is attached to it. |
| **OIDC federation** | GitHub Actions proves its identity to AWS with a short-lived token instead of stored keys. |
| **SCP** | Service Control Policy: an AWS Organizations guardrail that restricts every identity in member accounts, including root. |
| **CloudFront** | AWS's CDN; gives the HTTPS URL and routes requests to S3 (UI) or EC2 (API). |
| **OAC** | Origin Access Control: lets only CloudFront read the private S3 website bucket. |
| **SSM Session Manager** | Browser or CLI shell access to the instance without SSH or open ports. |
| **User data** | The script an EC2 instance runs on first boot ([`user_data.sh`](../infra/user_data.sh)). |
| **Elastic IP** | A fixed public IP that stays the same across instance stop/start and replacement. |

---

## 3. The input data

### CRM (`crm_events.json`, 20 records)

| Field | Example | Notes |
|---|---|---|
| `crm_id` | `CRM-1001` | Source ID |
| `subject` | `Q1 Portfolio Review` | |
| `client_name`, `client_company` | `David Park`, `Meridian Capital` | null for internal meetings |
| `relationship_owner` | `Sarah Chen` | A person's name, not an email |
| `meeting_date`, `meeting_time` | `2025-03-10`, `14:00` | Separate fields; no timezone; no end time |
| `meeting_type` | `In-Person`, `Virtual`, `Internal` | |
| `location` | `HQ - Conference Room B` | |
| `notes` | free text | |
| `status` | `Confirmed`, `Scheduled`, `Completed`, `Cancelled`, `Tentative` | |
| `created_at` | `2025-02-28T09:15:00Z` | |

### Calendar (`calendar_events.json`, 22 records)

| Field | Example | Notes |
|---|---|---|
| `event_id` | `CAL-A1` | Source ID |
| `title` | `Q1 Portfolio Review - Meridian Capital` | Often names the client |
| `organizer` | `sarah.chen@firma.com` | |
| `attendees` | list of emails | |
| `start_time`, `end_time` | `2025-03-10T14:00:00` | Mostly no timezone; one record uses `Z` (UTC) |
| `location` | `Zoom - https://…` | |
| `description` | free text | |
| `is_recurring` | `true` / `false` | |
| `status` | `confirmed`, `tentative` | lower-case |
| `created_at` | timestamp | |

The two sources share **no common ID**, use **different field names**, **different time formats** and **different status vocabularies**. This is why normalization comes first.

---

## 4. Architecture

```
            ┌─────────────── FastAPI backend ───────────────────────────────────────────┐
crm.json ──▶│ Loader ─▶ Normalize ─▶ Group duplicates ─▶ Match ─▶ Merge ─▶ In-memory store │──▶ REST /api/*
cal.json ──▶│ (file|S3)  (+ issues)    (per source)    (score)  (provenance)                │
            └────────────────────────────────────────────────────────────────────────────┘
                                                                                  │ JSON
                                                                        Angular + Material UI
```

| Layer | Technology | Location |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic, rapidfuzz | [`backend/app/`](../backend/app/) |
| Frontend | Angular 21 (standalone, signals), Angular Material | [`frontend/src/app/`](../frontend/src/app/) |
| Local runtime | Docker Compose (backend + nginx serving the UI and proxying `/api`) | [`docker-compose.yml`](../docker-compose.yml) |
| Cloud | AWS CDK: S3, CloudFront, EC2 (Ubuntu + Docker), CloudWatch | [`infra/`](../infra/) |
| CI/CD | GitHub Actions | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) |

### Backend modules

| Module | Responsibility |
|---|---|
| `config.py` | Settings from environment variables |
| `ingest/loaders.py` | Read the JSON from disk or S3, always decoding as UTF-8 |
| `ingest/parsing.py` | Tolerant parsers: dates, times, timestamps, emails, location kind, placeholders |
| `ingest/normalize.py` | Raw record → `NormalizedEvent` + `DataIssue`s |
| `ingest/pipeline.py` | `ingest()`: load and normalize both sources |
| `reconcile/similarity.py` | Text helpers: tokens, company words, name → email forms, location similarity |
| `reconcile/duplicates.py` | Same-source duplicate grouping |
| `reconcile/scoring.py` | Match score, weights, threshold, reasons |
| `reconcile/matcher.py` | One-to-one assignment of CRM and Calendar records |
| `reconcile/merger.py` | Per-field rules, field status, conflicts |
| `reconcile/service.py` | `reconcile()`: the whole pipeline, sorted output |
| `store.py` | In-memory result, sync, summary |
| `api/routes.py` | REST endpoints |
| `models/` | `normalized.py`, `unified.py`, `api.py` |

---

## 5. The reconciliation pipeline

### Stage 1: Normalize

| Rule | Example |
|---|---|
| Naive timestamps are assumed firm-local (`America/New_York`); timestamps with an offset are converted to it | CAL-A4 `19:00Z` → **15:00 EDT** (US daylight saving began 9 Mar 2025) |
| Non-ISO dates are repaired; year-last means US month-day-year | CRM-1008 `03-15/2025` → 2025-03-15 |
| CRM date + time are combined into a start time; a missing time keeps only the date | CRM-1007 |
| Emails are lower-cased; `[at]` / `(at)` and `[dot]` are repaired; invalid values are set aside | `raj.patel[at]atlasvc.com` → `raj.patel@atlasvc.com`; `external-guests` set aside |
| External domains = attendee domains other than `firma.com` | `meridiancap.com` |
| Internal = CRM type `Internal`, or calendar attendees all on `firma.com` | CAL-A3 Weekly Team Sync |
| Location kind: Zoom / Teams / Meet / Webex / "virtual" / `http` → virtual; any other text → physical; none → from CRM type, else unknown | |
| Placeholders (`Multiple`, `TBD`, `N/A`, …) → unknown | CRM-1017 client |
| Empty strings → null; statuses → lower-case | |
| A record that can't be processed at all is kept with an `unparseable_record` error | Never dropped |

### Stage 2: Group duplicates (within one source)

Two records from the same source are the same meeting when **all** hold:

1. Same date.
2. Same owner (CRM) or organizer (Calendar).
3. Times overlap, or start within 60 minutes.
4. Same external party (shared client domain or company word). For internal meetings: titles ≥ 85% similar.

The **newest** record (`created_at`) is canonical; the rest are superseded. Example: CAL-A6 (created 10 Mar, 11:30, adds the client's CIO) supersedes CAL-A5 (created 2 Mar, 11:00). Recurring instances like CAL-A3 and CAL-A18 are on different dates and are never grouped.

### Stage 3: Match CRM ↔ Calendar

**Gate:** only records on the **same date** are compared.

| Signal | Weight | Scoring |
|---|---|---|
| Start time | 0.30 | 1.0 if equal, linearly down to 0 at 3 h apart; 0.5 if the CRM time is unknown |
| Client | 0.25 | 1.0 if the CRM client's name appears as an attendee email local-part (`kevin.obrien`); 0.7 if only the surname appears; both internal = 1.0; unknown client = 0.5 |
| Company | 0.20 | Fraction of distinctive company words (generic words like *Capital, Group, Partners* removed) found in the calendar title or an attendee domain |
| Owner | 0.10 | 1.0 if the CRM relationship owner is the calendar organizer or an attendee |
| Title | 0.10 | Fuzzy token-set similarity of subject and title |
| Location | 0.05 | Fuzzy similarity; 0.5 if either is missing |

**Assignment:** all pairs scoring **≥ 0.55** are candidates; they are accepted from the highest score down, and each record is used at most once. A duplicate group is matched through its best-scoring member. Unmatched records become single-source meetings.

On the provided data, true matches score **0.78–1.00**; the highest wrong same-day pair scores **0.46** (CRM-1017 *Fund VII closing dinner* vs CAL-A19 *Fund VII roadshow prep*).

### Stage 4: Merge

| Field | Source of truth | Why |
|---|---|---|
| Title | Calendar (CRM subject kept) | Calendar titles usually name the client |
| Date, start, end | Calendar | Scheduling system; has end times and explicit offsets |
| Attendees, recurrence | Calendar | Only the calendar has them |
| Location | Calendar; the CRM value fills in if missing; the more specific value wins | It's where attendees actually go or join |
| Client, company | CRM | Relationship system of record |
| Owner | CRM (calendar organizer shown) | |
| Meeting type | CRM, validated against the calendar location kind | |
| Notes | CRM first, calendar description kept | Complementary |
| Status | **Most restrictive wins**: cancelled > tentative > confirmed/scheduled/completed | Showing a cancelled meeting as live is the costlier mistake |

Every field keeps `crm` and `calendar` raw values, the winning `source`, a `status` and the `rule` text.

### Stage 5: Store

The result is held in memory and rebuilt on start-up and on `POST /api/sync`. A full sync takes about 4 ms. If a sync fails, the previous result is kept and the error is reported.

---

## 6. Data-quality issues

An **issue** is a problem inside **one** record, as opposed to a **conflict** between two records. Every issue has a record ID, field, code, severity, message and the raw value.

### Severities

| Severity | Meaning |
|---|---|
| `info` | Normalized silently; worth knowing |
| `warning` | Value was repaired or is missing; review advised |
| `error` | Value unusable |

### Issue codes

| Code | Raised when |
|---|---|
| `malformed_date_repaired` | A non-ISO date was successfully interpreted |
| `invalid_date` | The date is missing or can't be parsed |
| `missing_time` | CRM meeting time is null |
| `invalid_time` | CRM meeting time can't be parsed |
| `invalid_timestamp` | A calendar timestamp or `created_at` can't be parsed |
| `nonstandard_timestamp` | Parsed, but not in full ISO-8601 form (e.g. no seconds) |
| `timezone_converted` | A timestamp with an explicit offset was converted to firm-local time |
| `end_before_start` | Calendar end precedes start |
| `email_repaired` | An attendee email was fixed (`[at]` → `@`) |
| `invalid_attendee` | An attendee entry isn't an email |
| `missing_attendees` | No attendees listed |
| `missing_location` | No location given |
| `missing_field` | An external CRM meeting has no client name or company |
| `placeholder_value` | A placeholder such as `Multiple` was treated as unknown |
| `encoding_repaired` | Mis-encoded text (e.g. `â€”`) was repaired; a defensive check, not triggered by this data |
| `unparseable_record` | A record couldn't be processed at all; kept with this error |

### Issues in the provided data (11)

| Record | Field | Severity | Issue |
|---|---|---|---|
| CRM-1007 | meeting_time | warning | Missing time |
| CRM-1008 | meeting_date | warning | `03-15/2025` → 2025-03-15 |
| CRM-1017 | client_name | info | `Multiple` treated as unknown |
| CAL-A4 | start_time | info | `19:00Z` → 15:00 EDT |
| CAL-A4 | end_time | info | `20:30Z` → 16:30 EDT |
| CAL-A11 | end_time | info | `2025-03-14T20:00` not full ISO |
| CAL-A11 | attendees | warning | No attendees |
| CAL-A11 | location | info | No location |
| CAL-A15 | location | info | No location |
| CAL-A16 | attendees | warning | `raj.patel[at]atlasvc.com` repaired |
| CAL-A20 | attendees | warning | `external-guests` is not an email |

Total: 5 warnings, 6 info, 0 errors.

---

## 7. Conflicts

### Field statuses

Every field of every unified meeting gets one status:

| Status | Meaning | Example |
|---|---|---|
| `agree` | Both sources give the same value | Same start time |
| `equivalent` | Different wording, same meaning | `HQ - Conference Room B` vs `Conference Room B`; `The Palm - DC` vs `The Palm Restaurant`; `completed` vs `confirmed`; any two virtual locations |
| `refinement` | One value is a more specific form of the other; the specific one wins | `NYC Office` → `NYC Office - 12th Floor` |
| `complementary` | Different but not contradictory | Titles; CRM notes vs calendar description; owner vs a different organizer |
| `filled` | One source was empty; the other's value is used | CRM-1007 time taken from the calendar |
| `conflict` | The sources contradict each other | See below |
| `single_source` | Only one source has this field (or the meeting) | Attendees, end time, client |

### What counts as a conflict

| Field | Rule | Severity |
|---|---|---|
| `start` | Start times differ at all | warning |
| `location` | Physical vs virtual, or two genuinely different places (not equivalent, not a refinement) | warning |
| `meeting_type` | CRM type contradicts the calendar location kind (not reported separately if the location conflict already covers it) | warning |
| `status` | States differ in restrictiveness | **error** if either side is cancelled, otherwise warning |

### Explained conflict messages

The merger adds a likely cause when the data supports one:

- **Daylight saving:** a 1-hour gap against a calendar time given in UTC → "a 1-hour gap usually means one system ignored daylight saving" (CRM-1004).
- **Reschedule:** a superseded duplicate has the CRM's time → "the meeting was likely rescheduled" (CRM-1005 / CAL-A5).

---

## 8. Results on the provided data

**42 records → 24 meetings:** 17 matched, 3 CRM-only (1003, 1010, 1020), 4 Calendar-only (A3, A11, A18, A19). **5 meetings with conflicts**, **1 duplicate group**, **11 issues**.

| Meeting | Conflict | Explanation |
|---|---|---|
| CRM-1002 + CAL-A2 | location | CRM In-Person at NYC Office vs Calendar Zoom |
| CRM-1004 + CAL-A4 | start | 14:00 vs 15:00, the daylight-saving trap |
| CRM-1005 + CAL-A6 (+A5) | start | 11:00 vs 11:30, likely rescheduled |
| CRM-1009 + CAL-A10 | status (error) | CRM cancelled vs Calendar confirmed; shown as cancelled |
| CRM-1016 + CAL-A17 | start | 13:00 vs 15:00 |

---

## 9. Data model

### `NormalizedEvent` (one per source record)

`source`, `source_id`, `title`, `start`, `end`, `date`, `time_known`, `client_name`, `client_company`, `owner_name`, `organizer_email`, `attendees`, `unparsed_attendees`, `external_domains`, `is_internal`, `location`, `location_kind`, `meeting_type`, `status`, `notes`, `is_recurring`, `created_at`, `raw` (the untouched record), `issues`.

### `UnifiedMeeting` (one per real meeting)

| Field | Content |
|---|---|
| `id` | Stable hash of the sorted source IDs |
| `source_ids` | `{"crm": [...], "calendar": [...]}` |
| `sources` | Which sources contributed |
| `fields` | One `FieldValue` per field: `title, date, start, end, client_name, client_company, owner, attendees, location, meeting_type, status, notes, is_recurring` |
| `conflicts` | List of `Conflict` |
| `match` | `MatchInfo`: `confidence`, `reasons`, `breakdown` (score per signal), `scored_pair` |
| `duplicates` | `DuplicateInfo`: `source`, `canonical_id`, `superseded_ids`, `reason` |
| `issues` | All issues of the contributing records |

### `FieldValue`

`value` (unified), `source` (where it came from), `crm` and `calendar` (what each said), `status` (§7), `rule` (why).

### `Conflict`

`field`, `severity`, `message`, `crm`, `calendar`.

---

## 10. REST API

Base path `/api`. Interactive docs at `/docs` (Swagger) and the schema at `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `status`, `data_source`, `ready`, `last_error` |
| GET | `/api/summary` | Counts: records per source, meetings, matched, CRM-only, Calendar-only, with conflicts, conflicts per field, duplicate groups, issues per severity |
| GET | `/api/meetings` | List of meetings (compact rows) |
| GET | `/api/meetings/{id}` | One meeting in full, plus `raw_records` (the untouched source records) |
| GET | `/api/issues` | All data-quality issues, each with its `meeting_id` |
| POST | `/api/sync` | Re-read both sources and rebuild; returns the summary |

**`/api/meetings` filters:** `source` = `crm` (CRM-only) / `calendar` (Calendar-only) / `both` (matched); `has_conflicts=true|false`; `status`; `from`, `to` (`YYYY-MM-DD`); `q` (searches title, client, company, owner, location and source IDs).

**`/api/issues` filters:** `severity`, `source`.

**Status codes:** `200` OK; `404` unknown meeting ID; `503` data not loaded yet (start-up sync failed); `502` a requested sync failed (previous data is kept).

---

## 11. User interface

| Page | URL | Shows |
|---|---|---|
| **Meetings** | `/meetings` | Summary cards; filters (All / Matched / CRM only / Calendar only, *Conflicts only*, search); table with date and time, title and client, owner, location, status, **source badges** (purple CRM, teal Calendar; hover for record IDs), duplicate icon, conflict count coloured by severity, issue count; **Re-sync** button |
| **Meeting detail** | `/meetings/{id}` | Conflict messages; duplicate info; **field-by-field table** (unified value tagged *from CRM* / *from Calendar*, *CRM said*, *Calendar said*, status with the rule on hover); match confidence, reasons and per-signal bars; issues; raw source records |
| **Data issues** | `/issues` | Every issue, filterable by severity, linked to its meeting |

The toolbar shows **API ready / not ready / unreachable**. All times are shown in **firm-local time** regardless of the viewer's timezone, so a viewer in India sees the same 14:00 the CRM recorded and the timezone conflicts make sense.

---

## 12. Configuration

Environment variables read by the backend ([`config.py`](../backend/app/config.py)):

| Variable | Default | Purpose |
|---|---|---|
| `DATA_SOURCE` | `file` | `file` or `s3` |
| `DATA_DIR` | `<repo>/data` | Folder with the JSON files (file mode) |
| `S3_BUCKET`, `S3_PREFIX` | none, `""` | Bucket and prefix (S3 mode; AWS uses prefix `data`) |
| `CRM_FILE`, `CALENDAR_FILE` | `crm_events.json`, `calendar_events.json` | File names |
| `FIRM_TIMEZONE` | `America/New_York` | Zone for naive timestamps |
| `FIRM_EMAIL_DOMAIN` | `firma.com` | Internal email domain |
| `CORS_ORIGINS` | `["http://localhost:4200"]` | Allowed browser origins when not served same-origin |

CDK context ([`cdk.json`](../infra/cdk.json)): `ubuntuRelease` (`26.04`), `instanceType` (`t3.micro`).

---

## 13. Running locally

| Mode | Command | URLs |
|---|---|---|
| One command | `docker compose up --build` | UI http://localhost:4200, API docs http://localhost:8000/docs |
| Backend only | `cd backend && pip install -r requirements-dev.txt && uvicorn app.main:app --reload` | http://localhost:8000 |
| Frontend dev | `cd frontend && npm ci && npm start` (proxies `/api` to :8000) | http://localhost:4200 |

---

## 14. Testing

`cd backend && pytest -q` runs **67 tests**:

| File | Covers |
|---|---|
| `test_parsing.py` | Every parser, including the DST conversion and each malformed value |
| `test_normalize.py` | Each known issue in the real files is detected and repaired; UTF-8 handling |
| `test_scoring.py` | Name and company helpers; true matches above and wrong same-day pairs below the threshold, incl. the tightest (0.46) |
| `test_reconcile.py` | **Golden test**: exact pairing of all 42 records, single-source sets, the duplicate group, recurring instances not merged, exact conflicts per meeting, provenance, stable IDs |
| `test_api.py` | Every endpoint and filter, the detail payload, re-sync, and behaviour when the data source is unreachable |

CI runs the tests and the production UI build on every push and pull request.

---

## 15. AWS infrastructure

```
 Browser ──HTTPS──▶ CloudFront ──┬── /* ─────────────────▶ S3 web bucket (Angular build, private, OAC)
                                 └── /api/*, /docs ──────▶ EC2 t3.micro · Ubuntu 26.04 · Docker (FastAPI :80)
                                                             │ IAM role (read-only)
                                                             ▼
                                                           S3 data bucket (data/*.json) · CloudWatch logs
```

### `EventSyncStack` resources

| Resource | Details |
|---|---|
| Data bucket | Private, encrypted, SSL-only; CDK uploads `data/*.json` |
| Web bucket | Private; CDK uploads the Angular build and invalidates the CloudFront cache |
| CloudFront distribution | HTTPS redirect; `/*` → web bucket via OAC with a CloudFront Function rewriting Angular routes to `index.html`; `/api/*`, `/docs`, `/openapi.json` → EC2 with caching disabled |
| VPC | One public subnet, no NAT gateway |
| Security group | Port 80 only from CloudFront's managed prefix list; no SSH |
| EC2 instance | t3.micro, Ubuntu 26.04 (AMI from Canonical's SSM parameter), 10 GB encrypted gp3, IMDSv2, CPU credits `standard` |
| Elastic IP | Stable origin address across stop/start and replacement |
| IAM role | SSM managed core, read data bucket and source zip, write logs; carries the permissions boundary |
| Log group | Container logs, one-week retention |
| Backend source asset | The backend folder zipped to S3 |

### How the backend gets onto the instance

On first boot, [`user_data.sh`](../infra/user_data.sh): adds 1 GB swap → installs Docker and the AWS CLI → downloads the backend zip → `docker build` → runs the container with `DATA_SOURCE=s3`, host networking, `--restart always` and CloudWatch logging → waits for `/api/health`. No local Docker and no container registry are needed. A backend code change produces a new zip, which replaces the instance with a freshly built one.

### `EventSyncFoundation` (one-time, [`foundation_app.py`](../infra/foundation_app.py))

| Resource | Purpose |
|---|---|
| `EventSyncPermissionsBoundary` | Upper limit for every role the app creates |
| `EventSyncCdkExecutionPolicy` | Scoped replacement for AdministratorAccess in CDK bootstrap |
| GitHub OIDC provider + `EventSyncGitHubDeployRole` | Keyless GitHub deploys; optional (`-c githubOidc=false`) |

---

## 16. Security and access model

| Principle | How |
|---|---|
| No long-lived keys | People use temporary sessions (SSO / federated console / CloudShell); CI uses OIDC |
| Least privilege for CI | The deploy role may only assume CDK bootstrap roles |
| Least privilege for CloudFormation | Scoped execution policy (available; the live PoC used CDK's default for speed) |
| Bounded app roles | Every app role carries the permissions boundary; removing it is denied |
| No public origin | EC2 accepts traffic only from CloudFront |
| No SSH | Access through SSM Session Manager |
| Private storage | Both buckets block public access, enforce SSL, are encrypted |
| Secrets in git | None; `AWS_details/` and `*.pem` are git-ignored; the account ID was removed before publishing |

**Governed account:** the PoC account belongs to an AWS Organization whose SCP denies creating OIDC providers (and offers no Identity Center or IAM console users). The live deployment therefore ran from AWS CloudShell on a temporary federated session. Enabling the GitHub OIDC provider in the organization moves deployments to GitHub without other changes.

---

## 17. CI/CD pipeline

[`deploy.yml`](../.github/workflows/deploy.yml):

| Job | When | Steps |
|---|---|---|
| **Test & build** | Every push and pull request | Backend tests → Angular production build → upload the build |
| **Deploy to AWS** | Push to `main` or manual run, and only when `AWS_DEPLOY_ROLE_ARN` is set | `production` environment (branch-restricted, optional reviewers) → download build → assume the deploy role via OIDC → `cdk deploy EventSyncStack` → app URL in the job summary |

Without the role variable, the deploy job is **skipped**, not failed. Deploys can't overlap (concurrency group).

**Manual deploy (current, from CloudShell):** `git pull` → `ng build` → `cdk deploy EventSyncStack`. The CloudFront URL never changes between deploys.

---

## 18. Operations

| Task | How |
|---|---|
| Check health | `https://<AppUrl>/api/health` |
| Refresh data | UI **Re-sync**, or `POST /api/sync` |
| Shell on the instance | `aws ssm start-session --target <InstanceId>` |
| First-boot log | `sudo tail -n 100 /var/log/event-sync-bootstrap.log` |
| Container log | `sudo docker logs backend`, or the CloudWatch log group |
| Pause (save cost) | `aws ec2 stop-instances --instance-ids <InstanceId>`; the UI stays up |
| Remove everything | `npx aws-cdk@2 destroy EventSyncStack` |

After a deploy that replaces the instance, the API returns 502/504 for about 3–5 minutes while the image builds.

---

## 19. Cost

Approximate on-demand prices per month, quoted for us-east-1; the live stack runs in Sydney (ap-southeast-2), where EC2 is roughly 25% more expensive:

| Item | Cost |
|---|---|
| EC2 t3.micro | ~$7.60 |
| Elastic IP / public IPv4 | ~$3.65 |
| 10 GB gp3 disk | ~$0.80 |
| S3, CloudFront, CloudWatch, Lambda helpers | cents (mostly free tier) |
| **Total** | **≈ $10–12** (≈ $4 with the instance stopped) |

Compared: ECS Fargate with a load balancer ≈ $37–40 a month.

---

## 20. Design decisions in brief

1. **Explainable rules, not ML**: every match shows its reasons and score breakdown.
2. **Same date is a hard gate**: removes almost all false candidates.
3. **Source of truth per field**: Calendar for scheduling, CRM for the relationship.
4. **Most restrictive status wins**: a cancellation is never hidden.
5. **Only real contradictions are conflicts**: cosmetic differences are classified, not flagged.
6. **Explained conflicts**: DST and reschedule hints where the data supports them.
7. **Nothing is dropped**: repairs are logged as issues; duplicates are kept as superseded.
8. **One timezone policy**: firm-local storage and display.
9. **In-memory store**: the result is cheap to rebuild; no database needed.
10. **EC2 + Docker on Ubuntu, image built on the instance**: cheapest option that matches local development.
11. **UI on S3 + CloudFront**: free HTTPS, survives instance stops, no CORS.
12. **No long-lived keys; least privilege; permissions boundary.**

---

## 21. Limitations and future work

**Limitations**
- A meeting moved to another day is not matched (same-date gate).
- Weights and threshold are tuned on this dataset only.
- Greedy assignment; dense data would warrant optimal (Hungarian) assignment.
- No authentication; single instance, not highly available; a backend redeploy causes a few minutes of API downtime.
- GitHub deploys are blocked by the organization SCP until the OIDC provider is allowed.

**Future work**
- Users confirm or reject matches and resolve conflicts, stored as overrides.
- Sync history; incremental or webhook ingestion.
- Authentication (Cognito / SSO).
- Custom domain and certificate.
- ECS Fargate behind a load balancer for availability and zero-downtime deploys.
- Separate dev and prod accounts; Identity Center from the organization's management account.

---

## 22. FAQ

**Why does a meeting show "Same start time" in its match reasons and also a time conflict?**
The match was scored on a superseded duplicate (CAL-A5, 11:00), while the values come from the newer canonical record (CAL-A6, 11:30). The detail page states the scored pair.

**Why is CRM-1004 a conflict when both seem to say 14:00?**
The calendar says `19:00Z`. On 13 March 2025 New York was already on daylight time (UTC−4), so that is 15:00 local, an hour after the CRM's 14:00.

**Why is "completed" vs "confirmed" not a conflict?**
Both describe a meeting that is on (or happened); they are lifecycle wording, not a contradiction.

**Why are the two Weekly Team Sync entries separate meetings?**
They are instances of a recurring meeting on different dates (11 and 18 March), not duplicates.

**Does the CloudFront URL change when I redeploy?**
No. It changes only if the stack is destroyed and recreated, renamed, or deployed to another account or region.

**Where do I change the matching behaviour?**
Weights, threshold and time decay are constants at the top of [`scoring.py`](../backend/app/reconcile/scoring.py); duplicate rules in [`duplicates.py`](../backend/app/reconcile/duplicates.py); field rules in [`merger.py`](../backend/app/reconcile/merger.py).

# AI Collaboration Log

The brief encourages AI assistance and asks for transparency. This file records how the work was split between me and the AI assistant (Claude Code), which decisions were mine, and where the AI was wrong and how that was caught.

## How we worked

The work ran as one conversation, in phases. Each phase ended with a report and nothing moved on until I approved it.

1. **Explain the problem, then plan.** I asked the assistant to explain the brief before writing any code. It read all 42 records and produced [`PLAN.md`](PLAN.md): the expected pairing of every record, a catalogue of data issues, design decisions, the API, the UI and the phases.
2. **Decide the infrastructure.** I set the constraints and the assistant priced the options (below).
3. **Implement phase by phase:** scaffold → ingest/normalize → reconcile → API → UI → docs → AWS. Each phase was verified by tests or by running the app before I was asked to continue.

## Decisions I made

| Topic | AI proposal | My decision | Why |
|---|---|---|---|
| Stack | — | Python + FastAPI, Angular, AWS | Given up front |
| Backend hosting | AWS App Runner or Lambda | **Not App Runner** (I ruled it out); asked for ECS vs EC2 **costs first** | Cost visibility before committing |
| Compute | Priced three options: ECS Fargate + ALB ≈ $37–40/mo, EC2 + Docker ≈ $10–12, ECS on EC2 ≈ $11–13 | **EC2 + Docker** | Cheapest; same container as local development |
| UI library | Material or plain CSS | **Angular Material** | Consistent components with little effort |
| Data location | Local files | **S3, provisioned by CDK**; local runs keep using files | Matches the target architecture without breaking the single-command local run |
| Deploy credentials | Create an IAM user with AdministratorAccess by hand | **Create it with CDK only** | Infrastructure as code for the identity too. The assistant made it least-privilege (assume CDK bootstrap roles only) and bootstrapped from CloudShell, since CDK cannot create its own first credentials |
| Existing AWS resources | Reuse my manually created instance and bucket, or let CDK own new ones | **Fully new via CDK** | Reproducible; `cdk destroy` cleans up everything |
| Instance OS | Amazon Linux 2023 | **Ubuntu only** | My preference |
| Capacity | — | I asked whether a t3.micro could run the containers | The assistant **measured** rather than guessed: ~65 MB for the backend, ~450–550 MB in total. It added a swap file and standard CPU credits as safeguards |
| Frontend placement | S3 + CloudFront | I asked why not EC2; accepted S3 + CloudFront | Free HTTPS, the UI survives instance stops, no CORS. Not a resource issue |

## What the AI did

- **Data analysis**: built the expected reconciliation table and spotted the less obvious traps:
  - CAL-A4's `19:00Z` is 15:00 **EDT** (DST began 9 March 2025), not 14:00;
  - CAL-A5/A6 is a duplicate but CAL-A3/A18 (recurring instances) is not;
  - `[at]` emails, `"Multiple"` as a client name.
- **Design**: explainable weighted scoring instead of ML; one source of truth per field; a classification that separates real conflicts from cosmetic differences; "most restrictive status wins".
- **Implementation** of the backend, frontend, tests and Docker setup.
- **Verification**: a golden test asserting the full expected outcome, and screenshots of every UI page rendered in headless Chrome.

## Where the AI was wrong, and how it was caught

These are listed because they show where review mattered.

1. **Misdiagnosed "corrupt" data.** In the initial analysis the assistant reported garbled text (`â€”`) in CRM-1009 and wrote a repair for it. While reviewing the *Data issues* page screenshot, it noticed the expected "encoding repaired" issue was missing. It checked the raw bytes: the file holds a correct UTF-8 em dash. The garbling came from its own inspection script, which read the file with Windows' default cp1252 encoding. The plan, test and README were corrected. The production loaders had always decoded as UTF-8, so the app was never affected, but this is exactly the trap the loaders exist to avoid.
2. **A match explanation that contradicted itself.** The Pinnacle meeting showed "Same start time (11:00)" next to a start-time conflict. The score came from the superseded duplicate CAL-A5, while the displayed values come from the canonical CAL-A6. Caught from the detail-page screenshot. Fix: the API now reports which record pair was scored, and the UI explains it.
3. **An over-optimistic claim.** The first README draft said the closest wrong match scored "about 0.35". Measuring before publishing showed **0.46** (CRM-1017 vs CAL-A19, which share "Fund VII"). The README now states the real margin, and a test guards it.
4. **A cost estimate that didn't fit the actual account.** The first estimate assumed a t4g.micro. My manually created instance was an `m7i-flex.large` (~$87/month), which the assistant flagged and which led to the "fully new via CDK" decision.
5. **Smaller bugs found by tests or the build:**
   - a Pydantic field named `date` shadowed the `date` type;
   - Windows Python has no timezone database (`tzdata` added);
   - timezone-aware vs naive sort keys;
   - strict template typing errors in Angular;
   - a CSS class name clash that made one table row taller.

## What I reviewed

- **Pairings and conflicts**: the expected reconciliation table in the plan before implementation, and the golden-test outcome against it.
- **Field rules**: every source-of-truth and conflict-classification rule.
- **AWS**: all cost and architecture choices. AWS credentials and key files were kept out of the repository (`AWS_details/` and `*.pem` are git-ignored, and the assistant did not open the key file).

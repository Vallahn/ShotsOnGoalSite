# ShotsOnGoalSite
Interactive Shots on Goal Map

***************************************************************************

An interactive shot chart for NHL play-by-play data, intended to pull live data as games are being played: filter by team, game, date range, shooter, goalie, shot type, result, and period, plotted on a rink. Fully self-hosted on AWS (free-trial friendly), managed with Terraform.

**Live data only covers what you ingest.** This isn't a hosted service with pre-loaded data — you're deploying your own pipeline that pulls from the NHL's public API into your own DynamoDB tables. Follow the backfill steps below to get a full season's worth of history rather than waiting for it to accumulate one live game at a time.

## Architecture

```
                    ┌─────────────────┐
  EventBridge  ───▶ │  pbp_ingest      │──▶ DynamoDB: NHL_PlayByPlay
  (every 1 min,     │  Lambda          │──▶ DynamoDB: NHL_Games
   evening window)  └─────────────────┘──▶ DynamoDB: NHL_GameRosters
                              ▲
                              │ enable/disable
  EventBridge  ───▶ ┌─────────────────┐
  (once daily)      │ schedule_toggle  │
                     │ Lambda           │
                     └─────────────────┘

  EventBridge  ───▶ ┌─────────────────┐
  (weekly)          │ roster_sync      │──▶ DynamoDB: NHL_Players
                     │ Lambda           │
                     └─────────────────┘

  Browser ──▶ CloudFront ──▶ S3 (React app)
     │
     └────▶ API Gateway ──▶ query_api Lambda ──▶ (reads all 4 tables)
```

Two IAM roles, split by privilege: the ingest Lambdas (`pbp_ingest`, `roster_sync`) can write; the `query_api` Lambda is read-only.

## Prerequisites

- An AWS account. A free-trial account works, but see [IAM permissions](#iam-permissions-you-may-need-to-add) below — some free-trial accounts start with a narrower IAM policy than this project needs.
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.3.0
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), configured (`aws configure`) with credentials for the account above
- [Node.js](https://nodejs.org/) (18+) and npm, for building the frontend
- Python 3.9+ with `pip`, for the one-time backfill scripts
- On Windows: PowerShell is assumed throughout (this project was built and tested primarily on Windows)

## Repo layout

```
terraform/
  main.tf              # all AWS infrastructure
  src/                  # Lambda source, zipped up by main.tf at apply time
    lambda_roster_sync.py
    lambda_pbp_ingest.py
    lambda_query_api.py
    lambda_schedule_toggle.py
scripts/                 # one-time backfill scripts, run locally
  backfill_full_season.py  # run this first, on a fresh deployment
  backfill_team_tricode.py
  backfill_games_table.py
  backfill_normalize_coords.py
  backfill_game_rosters.py
frontend/
  src/
    App.jsx
    api.js
    index.css
    components/
      FilterPanel.jsx
      RinkChart.jsx
      SearchableSelect.jsx
```

## Step 1: Deploy the backend infrastructure

```powershell
cd terraform
terraform init
terraform apply
```

Type `yes` when prompted. This provisions everything: four DynamoDB tables, four Lambda functions, an API Gateway HTTP API, EventBridge schedules, and an S3 + CloudFront static site (empty for now — the frontend gets deployed separately in Step 2).

Terraform's build step (`install_dependencies` / `copy_sources` in `main.tf`) pip-installs `requests` and zips up the `src/` folder into the Lambda deployment package automatically — you don't need to build that by hand.

When it finishes, note the outputs:

```powershell
terraform output
```

You'll need `api_endpoint`, `frontend_bucket_name`, and `cloudfront_domain_name` in Step 2.

### IAM permissions you may need to add

If `terraform apply` fails partway through with `AccessDeniedException` on `apigateway:POST`, `cloudfront:*`, or `s3:*`, your IAM user doesn't yet have permission for those services — common on a freshly created free-trial account. Attach these AWS-managed policies to the IAM user Terraform is running as (via IAM → Users → your user → Add permissions), then re-run `terraform apply` — it picks up right where it left off:

- `AmazonAPIGatewayAdministrator`
- `CloudFrontFullAccess`
- `AmazonS3FullAccess`
- `AmazonDynamoDBFullAccess`
- `AWSLambda_FullAccess`
- `IAMFullAccess` (needed because Terraform creates the Lambda execution roles themselves)

For a personal hobby project, attaching `AdministratorAccess` and moving on is also reasonable if you'd rather not manage a granular policy.

## Step 2: Deploy the frontend

```powershell
cd frontend
npm install
```

Create `.env.production` in the `frontend` folder (copy `.env.example` and fill in your real endpoint):

```
VITE_API_BASE_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com
```

Then build and deploy:

```powershell
npm run build
aws s3 sync dist/ s3://<frontend_bucket_name> --delete
aws cloudfront create-invalidation --distribution-id <cloudfront-distribution-id> --paths "/*"
```

Get the distribution ID with `terraform output cloudfront_distribution_id` (from inside `terraform/`), or from the CloudFront console.

Visit the `cloudfront_domain_name` output. You should see the site — with an empty shot map, since no data has been ingested yet.


## Step 3: Backfill historical data

The live pipeline (`pbp_ingest`) only ever captures games that are `LIVE` or in overtime/shootout (`CRIT`) at the moment it polls — it will never retroactively fill in games that already happened. To get a full season's worth of data, run the backfill script once per season you want.

```powershell
cd scripts
pip install boto3 requests --break-system-packages
python backfill_full_season.py 20252026
```

(Season codes follow the NHL's own convention: `20252026` for the 2025-26 season, `20262027` for 2026-27, etc.)

This enumerates every game across all 32 teams' schedules for that season, and for each one writes to `NHL_PlayByPlay`, `NHL_Games`, and `NHL_GameRosters` using the exact same logic as the live `pbp_ingest` Lambda — team attribution, direction-normalized coordinates, dressed rosters, all of it. It defaults to regular season + playoffs (skips preseason); edit `GAME_TYPES_TO_BACKFILL` near the top of the script if you want to change that.

This hits the NHL's public API once per game (~1,300+ calls for a full season), with a short delay between each — expect it to take a while. It's safe to stop and re-run: any game that already has play-by-play rows is skipped, so an interrupted run just picks up where it left off.


## Step 4: Verify live ingestion

The `pbp_ingest` Lambda runs every minute during an evening UTC window, but only actually does anything if a game is currently live. To confirm it's wired up correctly without waiting for a real game:

```powershell
aws logs tail /aws/lambda/nhl_pbp_ingest --since 10m
```

During the season, you should eventually see log lines like `Processed N play events across M live game(s).` During the off-season, you'll see `No active NHL games right now.` — that's expected.

### The daily schedule toggle

To avoid running the every-minute schedule 720 times a night, every night, year-round regardless of season, a second Lambda (`schedule_toggle`) runs once daily and enables/disables the `pbp_ingest` schedule based on whether there's actually a game on today's slate. You can test it manually:

```powershell
aws lambda invoke --function-name nhl_schedule_toggle --payload "{}" response.json
Get-Content response.json
aws events describe-rule --name nhl_pbp_ingest_schedule --query State
```

## Ongoing operation

- **Roster sync** runs weekly, keeping `NHL_Players` (current rosters) up to date.
- **PBP ingest** runs every minute in-season (see above), self-correcting as a game progresses since writes are idempotent.
- **Schedule toggle** runs once daily to turn PBP ingest on/off for the day.

Nothing else needs to run manually once deployed.

## Cost

Everything here is pay-per-request or within AWS's free tier: DynamoDB on-demand, Lambda (1M free requests/month), API Gateway HTTP API, S3, and CloudFront (1TB free egress/month for 12 months on a new account). At hobby-project traffic, expect this to run at or near $0/month. The main things that scale with usage are DynamoDB read costs on the public `/shots` endpoint and CloudFront egress if the site gets meaningfully popular — both are currently bounded somewhat by the API Gateway stage's throttle limits (10 req/s sustained, 20 burst).

## Known limitations

- **Shot coordinates are direction-normalized using an empirical method**, not an official API field — each period's attacking direction is inferred from where the home team's own shots cluster. This is generally reliable with a full period's worth of shots, but can be less certain for early-game snapshots during live ingestion (self-corrects as more shots come in) or unusually low-shot periods.
- **Player team attribution depends on context.** When no specific game is selected, shooter/goalie pickers use `NHL_Players`, which only reflects *current* rosters — a player who's since retired or left the league entirely won't appear. Selecting specific games instead uses `NHL_GameRosters`, which correctly reflects who actually played for which team in that exact game, regardless of subsequent trades.
- **Shootout events are excluded from direction normalization**, since both teams shoot at the same net in a shootout — home/away attacking-side logic doesn't apply there.

## Tearing it down

Three tables (`NHL_PlayByPlay`, `NHL_Games`, `NHL_GameRosters`) have `prevent_destroy` set in `main.tf`, since they hold data that's expensive to rebuild. To actually destroy everything (e.g. decommissioning the project):

1. Remove the `lifecycle { prevent_destroy = true }` block from each of those three `aws_dynamodb_table` resources in `main.tf`.
2. Run `terraform apply` to apply that change.
3. Run `terraform destroy`.



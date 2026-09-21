"""
Runs once daily. Checks whether any NHL games are on today's slate and
enables or disables the nhl_pbp_ingest_schedule EventBridge rule
accordingly, so the every-minute PBP polling only actually runs on days
there's something to poll for -- instead of firing 720 times a night,
every night, year-round, regardless of season.
"""
import json
import logging
from datetime import datetime, timezone
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

events_client = boto3.client("events")
RULE_NAME = "nhl_pbp_ingest_schedule"


def games_scheduled_today():
    """True if there's at least one game on today's UTC-date slate.

    Originally checked /v1/score/now and compared each game's gameDate
    against today -- but /v1/score/now has its own internal "current
    game day" that lags the real calendar date by several hours (its own
    currentDate field can still read yesterday well into the next
    morning), so that comparison reliably found nothing every day this
    check runs (7am Central), not just on the off-season edge case the
    old comment above was guarding against.

    /v1/schedule/{date} doesn't have that lag -- it returns a given
    date's games correctly even weeks in advance -- so we ask it about
    today directly instead of relying on a "current" feed to have caught
    up yet.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"https://api-web.nhle.com/v1/schedule/{today}"
    res = requests.get(url, timeout=8)
    res.raise_for_status()
    payload = res.json()

    for day in payload.get("gameWeek", []):
        if day.get("date") == today:
            return len(day.get("games", [])) > 0
    return False


def lambda_handler(event, context):
    try:
        has_games = games_scheduled_today()
    except Exception as e:
        # Fail safe: if the schedule check itself fails, leave ingestion
        # ENABLED rather than risk silently missing a game night because
        # of an unrelated NHL API hiccup.
        logger.error(f"Error checking today's schedule, defaulting to enabled: {e}")
        has_games = True

    if has_games:
        events_client.enable_rule(Name=RULE_NAME)
        msg = "Games scheduled today -- pbp_ingest_schedule ENABLED."
    else:
        events_client.disable_rule(Name=RULE_NAME)
        msg = "No games scheduled today -- pbp_ingest_schedule DISABLED."

    logger.info(msg)
    return {"statusCode": 200, "body": json.dumps(msg)}

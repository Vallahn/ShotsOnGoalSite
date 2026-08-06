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
    """True only if a game's own gameDate matches today's actual date.

    /v1/score/now does NOT reliably return an empty games list during
    the off-season -- it can return a preview of the next scheduled game
    day instead (e.g. season-opener night), still tagged gameState "FUT",
    weeks or months before it actually happens. So we can't just check
    whether the array is non-empty; we have to check that at least one
    game's gameDate is genuinely today.
    """
    url = "https://api-web.nhle.com/v1/score/now"
    res = requests.get(url, timeout=8)
    res.raise_for_status()
    payload = res.json()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    games = payload.get("games", [])
    return any(g.get("gameDate") == today for g in games)


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

"""
Spot-checks NHL_PlayByPlay against the NHL's own official boxscore, to
answer a concrete question: is last season's backfilled data actually
complete, or does it have gaps from the GSI/empty-team_tricode bug that
was breaking backfill_by_date.py (fixed) and, likely, backfill_full_season.py
and lambda_pbp_ingest.py (not yet fixed -- see below)?

For each sampled game, compares:
  - "shots on goal" (SOG) per team, from the NHL's own boxscore endpoint
    (the same number you'd see on NHL.com) -- this only counts
    shot-on-goal + goal, not missed/blocked attempts, since that's the
    stat the NHL actually publishes and lets us check against.
  - the same count, computed from whatever is currently sitting in your
    NHL_PlayByPlay DynamoDB table for that exact game_id.

A mismatch means that game is missing shot events in DynamoDB (a real
gap from the backfill bug) -- an exact match means that game backfilled
cleanly.

Read-only: does not write anything to DynamoDB or call any AWS mutating
API. Safe to run repeatedly / for spot-checks.

Usage:
    pip install boto3 requests --break-system-packages

    # Check N random regular-season/playoff games from a season:
    python verify_backfill.py --season 20252026 --sample 15

    # Or check specific games you care about:
    python verify_backfill.py --game-ids 2025020450,2025020612
"""
import argparse
import random
import time

import boto3
import requests
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")

TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH",
    "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS", "SEA",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG",
]

# gameType: 1 = preseason, 2 = regular season, 3 = playoffs -- matches
# backfill_full_season.py's GAME_TYPES_TO_BACKFILL, since that's what a
# season backfill actually targets.
GAME_TYPES_TO_CHECK = {2, 3}

SOG_EVENT_TYPES = {"shot-on-goal", "goal"}


def get_season_game_ids(season):
    game_ids = set()
    for team in TEAMS:
        url = f"https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                continue
            for g in res.json().get("games", []):
                if g.get("gameType") in GAME_TYPES_TO_CHECK and g.get("gameState") == "OFF":
                    # OFF = completed -- only check games that are actually
                    # finished, so partial live data doesn't look like a gap.
                    game_ids.add(g["id"])
        except Exception as e:
            print(f"  {team}: error fetching schedule: {e}")
        time.sleep(0.1)
    return sorted(game_ids)


def official_sog(game_id):
    """Official shots-on-goal per team, from the NHL's own boxscore --
    the same number displayed on NHL.com for this game."""
    url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()
    home = data.get("homeTeam", {})
    away = data.get("awayTeam", {})
    return {
        home.get("abbrev", "HOME"): home.get("sog", 0),
        away.get("abbrev", "AWAY"): away.get("sog", 0),
    }


def dynamo_sog(game_id):
    """SOG per team, counted from whatever's actually in NHL_PlayByPlay
    for this game_id right now."""
    resp = PBP_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id))
    items = resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = PBP_TABLE.query(
            KeyConditionExpression=Key("game_id").eq(game_id),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))

    counts = {}
    total_rows = len(items)
    for item in items:
        if item.get("event_type") in SOG_EVENT_TYPES:
            team = item.get("team_tricode", "")
            counts[team] = counts.get(team, 0) + 1
    return counts, total_rows


def check_game(game_id):
    try:
        official = official_sog(game_id)
    except Exception as e:
        print(f"  {game_id}: couldn't fetch official boxscore ({e}), skipping")
        return None

    dynamo, total_rows = dynamo_sog(game_id)

    ok = True
    lines = []
    for team, official_count in official.items():
        dynamo_count = dynamo.get(team, 0)
        mark = "OK" if dynamo_count == official_count else "MISMATCH"
        if dynamo_count != official_count:
            ok = False
        lines.append(f"      {team}: official SOG={official_count}  dynamo SOG={dynamo_count}  [{mark}]")

    status = "COMPLETE" if ok else "GAP DETECTED"
    print(f"  {game_id} ({total_rows} total rows in DynamoDB for this game): {status}")
    for line in lines:
        print(line)
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", help="e.g. 20252026 -- samples random completed games from this season")
    parser.add_argument("--sample", type=int, default=15, help="how many games to sample (with --season)")
    parser.add_argument("--game-ids", help="comma-separated game_ids to check directly, instead of sampling")
    args = parser.parse_args()

    if args.game_ids:
        game_ids = [int(g.strip()) for g in args.game_ids.split(",") if g.strip()]
    elif args.season:
        print(f"Enumerating completed games for season {args.season}...")
        all_ids = get_season_game_ids(args.season)
        print(f"Found {len(all_ids)} completed regular-season/playoff games.")
        game_ids = sorted(random.sample(all_ids, min(args.sample, len(all_ids))))
    else:
        parser.error("Provide either --season or --game-ids")

    print(f"Checking {len(game_ids)} game(s)...\n")

    complete = 0
    gapped = 0
    skipped = 0
    for gid in game_ids:
        result = check_game(gid)
        if result is None:
            skipped += 1
        elif result:
            complete += 1
        else:
            gapped += 1
        time.sleep(0.15)

    print(f"\nDone. {complete} complete, {gapped} with gaps, {skipped} skipped (couldn't fetch official data), out of {len(game_ids)} checked.")

"""
One-time backfill: populates NHL_GameRosters for games ingested before that
table existed, so the shooter/goalie pickers work without any live NHL API
calls for your existing season data. Safe to re-run.

Run locally, once:
    pip install boto3 requests
    python backfill_game_rosters.py
"""
import time
import boto3
import requests
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")
GAME_ROSTERS_TABLE = dynamodb.Table("NHL_GameRosters")


def distinct_game_ids():
    ids = set()
    scan_kwargs = {"ProjectionExpression": "game_id"}
    while True:
        resp = PBP_TABLE.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            ids.add(int(item["game_id"]))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return ids


def already_has_roster(game_id):
    resp = GAME_ROSTERS_TABLE.query(
        KeyConditionExpression=Key("game_id").eq(game_id),
        Limit=1,
    )
    return len(resp.get("Items", [])) > 0


def backfill_game(game_id):
    url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"
    res = requests.get(url, timeout=10)
    if res.status_code != 200:
        print(f"  skip {game_id}: HTTP {res.status_code}")
        return

    data = res.json()
    home = data.get("homeTeam", {})
    away = data.get("awayTeam", {})
    team_id_to_tricode = {
        home.get("id"): home.get("abbrev", ""),
        away.get("id"): away.get("abbrev", ""),
    }

    written = 0
    with GAME_ROSTERS_TABLE.batch_writer() as batch:
        for spot in data.get("rosterSpots", []):
            player_id = spot.get("playerId")
            if player_id is None:
                continue
            batch.put_item(
                Item={
                    "game_id": game_id,
                    "player_id": player_id,
                    "first_name": spot.get("firstName", {}).get("default", ""),
                    "last_name": spot.get("lastName", {}).get("default", ""),
                    "position": spot.get("positionCode", ""),
                    "team_tricode": team_id_to_tricode.get(spot.get("teamId"), ""),
                }
            )
            written += 1

    print(f"  game {game_id}: wrote {written} roster spots")


if __name__ == "__main__":
    print("Finding distinct game_ids in NHL_PlayByPlay...")
    ids = sorted(distinct_game_ids())
    print(f"Found {len(ids)} games. Backfilling NHL_GameRosters...")
    for i, gid in enumerate(ids, 1):
        if already_has_roster(gid):
            continue
        backfill_game(gid)
        if i % 50 == 0:
            print(f"  ...{i}/{len(ids)}")
        time.sleep(0.2)
    print("Done.")

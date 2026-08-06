"""
One-time backfill: populates NHL_Games from the distinct game_ids already
sitting in NHL_PlayByPlay. Needed because lambda_pbp_ingest.py only writes
to NHL_Games while polling *live* games -- a season backfilled directly
into NHL_PlayBywPlay by some other means never touches this table.

Run locally, once:
    pip install boto3 requests
    python backfill_games_table.py
"""
import time
import boto3
import requests

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")
GAMES_TABLE = dynamodb.Table("NHL_Games")


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


def upsert_game(game_id):
    url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"
    res = requests.get(url, timeout=10)
    if res.status_code != 200:
        print(f"  skip {game_id}: HTTP {res.status_code}")
        return

    data = res.json()
    home = data.get("homeTeam", {})
    away = data.get("awayTeam", {})
    game_date = data.get("gameDate") or (data.get("startTimeUTC", "")[:10])

    GAMES_TABLE.put_item(
        Item={
            "game_id": game_id,
            "gsi_type": "GAME",
            "game_date": game_date,
            "season": str(data.get("season", "")),
            "game_state": data.get("gameState", ""),
            "home_team": home.get("abbrev", ""),
            "away_team": away.get("abbrev", ""),
            "home_team_id": home.get("id", 0),
            "away_team_id": away.get("id", 0),
            "home_score": home.get("score", 0),
            "away_score": away.get("score", 0),
        }
    )


if __name__ == "__main__":
    print("Finding distinct game_ids in NHL_PlayByPlay...")
    ids = distinct_game_ids()
    print(f"Found {len(ids)} games. Backfilling NHL_Games...")
    for i, gid in enumerate(sorted(ids), 1):
        upsert_game(gid)
        if i % 50 == 0:
            print(f"  ...{i}/{len(ids)}")
        time.sleep(0.2)  # be polite to the NHL API
    print("Done.")

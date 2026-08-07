"""
Full initial backfill: populates NHL_PlayByPlay, NHL_Games, and
NHL_GameRosters from scratch for an entire season, using the exact same
ingestion logic as lambda_pbp_ingest.py (team attribution, direction-
normalized coordinates, game metadata, dressed rosters) -- so a fresh
deployment doesn't need to also run the other backfill_*.py scripts
afterward. Those scripts remain useful if you already have partial data
and only need to add one specific field.

Run locally, once per season you want to backfill:
    pip install boto3 requests
    python backfill_full_season.py 20252026

Safe to re-run / resume: games that already have play-by-play rows are
skipped, so an interrupted run can just be started again.
"""
import sys
import time
import boto3
import requests
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")
GAMES_TABLE = dynamodb.Table("NHL_Games")
GAME_ROSTERS_TABLE = dynamodb.Table("NHL_GameRosters")

TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH",
    "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS", "SEA",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG",
]

SHOT_EVENT_TYPES = {"shot-on-goal", "goal", "missed-shot", "blocked-shot"}

# gameType: 1 = preseason, 2 = regular season, 3 = playoffs
GAME_TYPES_TO_BACKFILL = {2, 3}


def get_season_game_ids(season):
    """Enumerates every game_id for the season by walking each team's full
    schedule and deduping (every game appears on two teams' schedules)."""
    game_ids = set()
    for team in TEAMS:
        url = f"https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                print(f"  {team}: HTTP {res.status_code}, skipping")
                continue
            data = res.json()
            for g in data.get("games", []):
                if g.get("gameType") in GAME_TYPES_TO_BACKFILL:
                    game_ids.add(g["id"])
        except Exception as e:
            print(f"  {team}: error fetching schedule: {e}")
        time.sleep(0.15)
    return game_ids


def already_ingested(game_id):
    resp = PBP_TABLE.query(KeyConditionExpression=Key("game_id").eq(game_id), Limit=1)
    return len(resp.get("Items", [])) > 0


def compute_period_attacking_sides(plays, home_abbrev, team_id_to_tricode):
    """Which side ("left"/"right") the HOME team attacks, per period,
    derived from where the home team's own shot attempts cluster --
    the NHL API's homeTeamDefendingSide field isn't reliably present."""
    tallies = {}
    all_periods = set()

    for play in plays:
        period_desc = play.get("periodDescriptor", {})
        period = period_desc.get("number")
        if period:
            all_periods.add(period)

        if play.get("typeDescKey") not in SHOT_EVENT_TYPES:
            continue
        if period_desc.get("periodType") == "SO":
            continue  # shootout: both teams shoot at the same net

        details = play.get("details", {})
        x = details.get("xCoord")
        if x is None or x == 0:
            continue

        owner_team_id = details.get("eventOwnerTeamId")
        if team_id_to_tricode.get(owner_team_id, "") != home_abbrev:
            continue

        bucket = tallies.setdefault(period, {"pos": 0, "neg": 0})
        bucket["pos" if x > 0 else "neg"] += 1

    sides = {
        period: ("right" if counts["pos"] >= counts["neg"] else "left")
        for period, counts in tallies.items()
        if (counts["pos"] + counts["neg"]) > 0
    }

    known_periods = sorted(sides.keys())
    if known_periods:
        for period in sorted(all_periods):
            if period in sides:
                continue
            nearest = min(known_periods, key=lambda p: abs(p - period))
            flips = abs(period - nearest) % 2 == 1
            sides[period] = (
                ("left" if sides[nearest] == "right" else "right") if flips else sides[nearest]
            )

    return sides


def normalized_coords(x, y, shooting_team, home_team_tricode, home_attacking_side):
    if home_attacking_side not in ("left", "right"):
        return None, None
    is_home = shooting_team == home_team_tricode
    attacking_right = (home_attacking_side == "right") if is_home else (home_attacking_side == "left")
    if attacking_right:
        return x, y
    return -x, -y


def backfill_game(game_id):
    url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"
    res = requests.get(url, timeout=10)
    if res.status_code != 200:
        print(f"  skip {game_id}: HTTP {res.status_code}")
        return 0

    data = res.json()
    home_team = data.get("homeTeam", {})
    away_team = data.get("awayTeam", {})
    home_abbrev = home_team.get("abbrev", "")
    team_id_to_tricode = {
        home_team.get("id"): home_abbrev,
        away_team.get("id"): away_team.get("abbrev", ""),
    }
    plays = data.get("plays", [])

    # --- NHL_Games ---
    game_date = data.get("gameDate") or (data.get("startTimeUTC", "")[:10])
    GAMES_TABLE.put_item(
        Item={
            "game_id": game_id,
            "gsi_type": "GAME",
            "game_date": game_date,
            "season": str(data.get("season", "")),
            "game_state": data.get("gameState", ""),
            "home_team": home_abbrev,
            "away_team": away_team.get("abbrev", ""),
            "home_team_id": home_team.get("id", 0),
            "away_team_id": away_team.get("id", 0),
            "home_score": home_team.get("score", 0),
            "away_score": away_team.get("score", 0),
        }
    )

    # --- NHL_GameRosters ---
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

    # --- NHL_PlayByPlay ---
    period_attacking_sides = compute_period_attacking_sides(plays, home_abbrev, team_id_to_tricode)
    events_written = 0

    with PBP_TABLE.batch_writer() as batch:
        for play in plays:
            event_id = play.get("eventId")
            if event_id is None:
                continue

            details = play.get("details", {})
            owner_team_id = details.get("eventOwnerTeamId")
            shooting_team = team_id_to_tricode.get(owner_team_id, "")

            raw_x = details.get("xCoord")
            raw_y = details.get("yCoord")
            period_num = play.get("periodDescriptor", {}).get("number")
            home_attacking_side = period_attacking_sides.get(period_num)
            norm_x, norm_y = (None, None)
            if raw_x is not None and raw_y is not None:
                norm_x, norm_y = normalized_coords(
                    raw_x, raw_y, shooting_team, home_abbrev, home_attacking_side
                )

            batch.put_item(
                Item={
                    "game_id": game_id,
                    "event_id": event_id,
                    "event_type": play.get("typeDescKey", ""),
                    "period": play.get("periodDescriptor", {}).get("number", 0),
                    "time_in_period": play.get("timeInPeriod", ""),
                    "x_coord": str(details.get("xCoord", "")),
                    "y_coord": str(details.get("yCoord", "")),
                    "norm_x": str(norm_x) if norm_x is not None else "",
                    "norm_y": str(norm_y) if norm_y is not None else "",
                    "shot_type": details.get("shotType", ""),
                    "shooter_id": details.get("shootingPlayerId")
                    or details.get("scoringPlayerId")
                    or 0,
                    "goalie_id": details.get("goalieInNetId", 0),
                    "team_tricode": shooting_team,
                    "timestamp": "",  # not meaningful for historical backfill
                }
            )
            events_written += 1

    return events_written


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python backfill_full_season.py <season, e.g. 20252026>")
        sys.exit(1)

    season = sys.argv[1]
    print(f"Enumerating games for season {season}...")
    game_ids = sorted(get_season_game_ids(season))
    print(f"Found {len(game_ids)} games.")

    total_events = 0
    for i, gid in enumerate(game_ids, 1):
        if already_ingested(gid):
            continue
        try:
            events = backfill_game(gid)
            total_events += events
        except Exception as e:
            print(f"  error on game {gid}: {e}")
        if i % 25 == 0:
            print(f"  ...{i}/{len(game_ids)} games checked")
        time.sleep(0.2)

    print(f"Done. Wrote {total_events} play events across {len(game_ids)} games.")

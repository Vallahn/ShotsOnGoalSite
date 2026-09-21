
import sys
import time
from datetime import datetime, timezone

import boto3
import requests

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")
GAMES_TABLE = dynamodb.Table("NHL_Games")
GAME_ROSTERS_TABLE = dynamodb.Table("NHL_GameRosters")

SHOT_EVENT_TYPES = {"shot-on-goal", "goal", "missed-shot", "blocked-shot"}


def get_game_ids_for_date(date_str):

    url = f"https://api-web.nhle.com/v1/schedule/{date_str}"
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    data = res.json()

    for day in data.get("gameWeek", []):
        if day.get("date") == date_str:
            return [g["id"] for g in day.get("games", [])]
    return []


def compute_period_attacking_sides(plays, home_abbrev, team_id_to_tricode):

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
    away_abbrev = away_team.get("abbrev", "")
    team_id_to_tricode = {
        home_team.get("id"): home_abbrev,
        away_team.get("id"): away_abbrev,
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
            "away_team": away_abbrev,
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
            # Only shots ever get read by the app (query API / frontend
            # both filter on SHOT_EVENT_TYPES), and non-shot plays (faceoffs,
            # period-start/end, stoppages, etc.) frequently have no
            # eventOwnerTeamId at all -- which would write team_tricode as
            # "", and DynamoDB rejects an empty string as a GSI key
            # (team-index is keyed on team_tricode), aborting the whole
            # batch. Skipping non-shot plays avoids that entirely.
            if play.get("typeDescKey") not in SHOT_EVENT_TYPES:
                continue

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
                    "timestamp": "",  # not meaningful for historical/catch-up backfill
                }
            )
            events_written += 1

    return events_written, home_abbrev, away_abbrev, data.get("gameState", "")


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print("Usage: python backfill_by_date.py [YYYY-MM-DD]  (defaults to today, UTC)")
        sys.exit(1)

    date_str = sys.argv[1] if len(sys.argv) == 2 else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"Fetching schedule for {date_str}...")
    game_ids = get_game_ids_for_date(date_str)
    print(f"Found {len(game_ids)} game(s) on {date_str}: {game_ids}")

    total_events = 0
    for gid in game_ids:
        try:
            events, home, away, state = backfill_game(gid)
            total_events += events
            print(f"  {gid} ({away} @ {home}, {state}): wrote {events} play events")
        except Exception as e:
            print(f"  error on game {gid}: {e}")
        time.sleep(0.2)

    print(f"Done. Wrote {total_events} play events across {len(game_ids)} games for {date_str}.")

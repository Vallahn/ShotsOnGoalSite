import json
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table("NHL_PlayByPlay")
GAMES_TABLE = dynamodb.Table("NHL_Games")
GAME_ROSTERS_TABLE = dynamodb.Table("NHL_GameRosters")


def get_active_game_ids():
    """Queries today's score endpoint for LIVE or CRIT (OT/SO) games."""
    url = "https://api-web.nhle.com/v1/score/now"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code != 200:
            return []

        data = res.json()
        games = data.get("games", [])
        return [g["id"] for g in games if g.get("gameState") in ["LIVE", "CRIT"]]
    except Exception as e:
        logger.error(f"Error fetching live games: {e}")
        return []


def upsert_game_metadata(game_id, data):
    """Writes/refreshes lightweight game metadata used to power the
    'choose a game / date range' picker on the website, without requiring
    a scan of the (much larger) play-by-play table."""
    try:
        home = data.get("homeTeam", {})
        away = data.get("awayTeam", {})
        game_date = data.get("gameDate") or (data.get("startTimeUTC", "")[:10])
        season = str(data.get("season", ""))

        GAMES_TABLE.put_item(
            Item={
                "game_id": game_id,
                "gsi_type": "GAME",  # constant partition for the date-range GSI
                "game_date": game_date,
                "season": season,
                "game_state": data.get("gameState", ""),
                "home_team": home.get("abbrev", ""),
                "away_team": away.get("abbrev", ""),
                "home_team_id": home.get("id", 0),
                "away_team_id": away.get("id", 0),
                "home_score": home.get("score", 0),
                "away_score": away.get("score", 0),
                "last_updated": datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Error upserting game metadata for {game_id}: {e}")


def upsert_game_roster(game_id, data):
    """Persists this game's actual dressed roster (rosterSpots), so the
    website's shooter/goalie pickers for a specific game don't need to
    hit the NHL API live -- and stay correct for players since traded,
    waived, or retired (unlike the NHL_Players current-roster table)."""
    try:
        home = data.get("homeTeam", {})
        away = data.get("awayTeam", {})
        team_id_to_tricode = {
            home.get("id"): home.get("abbrev", ""),
            away.get("id"): away.get("abbrev", ""),
        }

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
    except Exception as e:
        logger.error(f"Error upserting game roster for {game_id}: {e}")


SHOT_EVENT_TYPES = {"shot-on-goal", "goal", "missed-shot", "blocked-shot"}


def compute_period_attacking_sides(plays, home_abbrev, team_id_to_tricode):
    """
    Empirically determines which side of the ice ("left"/x<0 or
    "right"/x>0) the HOME team attacks in each period.

    We initially tried reading periodDescriptor.homeTeamDefendingSide
    directly from the API, but in practice it isn't reliably present on
    plays -- so instead we look at where the home team's own shot attempts
    cluster that period. With a full period's worth of shots, one side of
    the ice dominates clearly.
    """
    tallies = {}  # period_number -> {"pos": n, "neg": n}
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
            continue  # only tally the home team's own shots

        bucket = tallies.setdefault(period, {"pos": 0, "neg": 0})
        bucket["pos" if x > 0 else "neg"] += 1

    sides = {
        period: ("right" if counts["pos"] >= counts["neg"] else "left")
        for period, counts in tallies.items()
        if (counts["pos"] + counts["neg"]) > 0
    }

    # Fill in any period with no home-team shots yet (e.g. an early-game
    # snapshot) by alternating from the nearest period we do know --
    # teams switch ends every period.
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
    """
    Rotates (x, y) 180 degrees when needed so every shot reads as if the
    shooting team were attacking toward positive x -- letting a shot chart
    combine shots from either end of the ice, either team, and any period
    without them showing up mirrored across the rink.

    home_attacking_side: which side ("left"/"right") the HOME team attacks
    in this play's period, from compute_period_attacking_sides(), or None
    if it couldn't be determined (e.g. shootout, or no data that period).
    """
    if home_attacking_side not in ("left", "right"):
        return None, None

    is_home = shooting_team == home_team_tricode
    attacking_right = (home_attacking_side == "right") if is_home else (home_attacking_side == "left")

    if attacking_right:
        return x, y
    return -x, -y


def process_game(game_id):
    """Fetches and ingests one game's current play-by-play state. Runs
    concurrently across all active games (see lambda_handler) since each
    game involves its own independent HTTP fetch + DynamoDB writes --
    processing them one at a time was the main risk of this Lambda timing
    out on a night with several simultaneous live games."""
    pbp_url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"
    res = requests.get(pbp_url, timeout=8)

    if res.status_code != 200:
        return 0

    data = res.json()
    plays = data.get("plays", [])

    # Refresh lightweight game metadata (teams, date, score) every pass.
    # Cheap single-item write; keeps the games picker current live.
    upsert_game_metadata(game_id, data)

    # Refresh the dressed roster too -- cheap (~40 items), and self-
    # corrects over the game in the rare case of an emergency call-up.
    upsert_game_roster(game_id, data)

    # Map eventOwnerTeamId -> team tricode using this game's home/away
    # teams, so every play/shot can be filtered by team downstream.
    home_team = data.get("homeTeam", {})
    away_team = data.get("awayTeam", {})
    team_id_to_tricode = {
        home_team.get("id"): home_team.get("abbrev", ""),
        away_team.get("id"): away_team.get("abbrev", ""),
    }

    home_abbrev = home_team.get("abbrev", "")
    period_attacking_sides = compute_period_attacking_sides(plays, home_abbrev, team_id_to_tricode)

    events_processed = 0

    # Use batch_writer context manager for high-throughput DynamoDB writes
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

            item = {
                "game_id": game_id,  # Partition Key
                "event_id": event_id,  # Sort Key
                "event_type": play.get("typeDescKey", ""),
                "period": play.get("periodDescriptor", {}).get("number", 0),
                "time_in_period": play.get("timeInPeriod", ""),
                "x_coord": str(
                    details.get("xCoord", "")
                ),  # DynamoDB handles String / Decimal
                "y_coord": str(details.get("yCoord", "")),
                # Direction-normalized coordinates (all shots attack
                # toward +x), so the shot map can offer a
                # "normalize to one side" toggle. Blank when the API
                # didn't provide homeTeamDefendingSide for this play.
                "norm_x": str(norm_x) if norm_x is not None else "",
                "norm_y": str(norm_y) if norm_y is not None else "",
                "shot_type": details.get("shotType", ""),
                "shooter_id": details.get("shootingPlayerId")
                or details.get("scoringPlayerId")
                or 0,
                "goalie_id": details.get("goalieInNetId", 0),
                "team_tricode": shooting_team,
                "timestamp": datetime.utcnow().isoformat(),
            }

            batch.put_item(Item=item)
            events_processed += 1

    return events_processed


def lambda_handler(event, context):
    active_games = get_active_game_ids()

    if not active_games:
        logger.info("No active NHL games right now. Terminating execution.")
        return {
            "statusCode": 200,
            "body": json.dumps("No active games scheduled."),
        }

    total_events_processed = 0

    with ThreadPoolExecutor(max_workers=min(10, len(active_games))) as pool:
        futures = {pool.submit(process_game, gid): gid for gid in active_games}
        for future in as_completed(futures):
            gid = futures[future]
            try:
                total_events_processed += future.result()
            except Exception as e:
                logger.error(f"Error processing game {gid}: {e}")

    msg = f"Processed {total_events_processed} play events across {len(active_games)} live game(s)."
    logger.info(msg)

    return {"statusCode": 200, "body": json.dumps(msg)}

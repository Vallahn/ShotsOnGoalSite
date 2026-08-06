"""
API backend for the NHL Shot Map website.
Exposed behind API Gateway (HTTP API) with Lambda proxy integration.

Routes:
  GET /games    -> list games (optionally filtered by date range / team)
  GET /teams    -> static list of team tricodes/names
  GET /players  -> roster lookup, optionally filtered by team and/or position
  GET /shots    -> filtered play-by-play query (the shot map data)

Design note: DynamoDB doesn't support ad-hoc multi-attribute filtering
efficiently, so this handler always starts from the most *selective*
index available given the filters the user picked (specific games, a
team, a shooter, or a goalie), and applies any remaining filters as a
FilterExpression on top of that. If none of those are provided, it
refuses the request rather than Scan-ing the whole table.
"""

import json
import os
import decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb")
PBP_TABLE = dynamodb.Table(os.environ.get("PBP_TABLE_NAME", "NHL_PlayByPlay"))
PLAYERS_TABLE = dynamodb.Table(os.environ.get("PLAYERS_TABLE_NAME", "NHL_Players"))
GAMES_TABLE = dynamodb.Table(os.environ.get("GAMES_TABLE_NAME", "NHL_Games"))
GAME_ROSTERS_TABLE = dynamodb.Table(os.environ.get("GAME_ROSTERS_TABLE_NAME", "NHL_GameRosters"))

MAX_GAMES_PER_SHOTS_REQUEST = 40   # protects Lambda timeout / cost
MAX_ITEMS_RETURNED = 5000

TEAMS = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH",
    "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SJS", "SEA",
    "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG",
]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def _csv_param(params, name):
    val = params.get(name)
    if not val:
        return None
    return [v.strip() for v in val.split(",") if v.strip()]


def _build_shot_filters(params, exclude=frozenset()):
    """Builds a combined FilterExpression from optional query params,
    skipping any attribute already covered by a KeyConditionExpression
    (DynamoDB rejects filtering on a primary/index key attribute)."""
    filt = None

    def _and(new_cond):
        nonlocal filt
        filt = new_cond if filt is None else (filt & new_cond)

    team = params.get("team")
    if team and "team_tricode" not in exclude:
        _and(Attr("team_tricode").eq(team))

    shooter_id = params.get("shooter_id")
    if shooter_id and "shooter_id" not in exclude:
        _and(Attr("shooter_id").eq(int(shooter_id)))

    goalie_id = params.get("goalie_id")
    if goalie_id and "goalie_id" not in exclude:
        _and(Attr("goalie_id").eq(int(goalie_id)))

    shot_types = _csv_param(params, "shot_type")
    if shot_types:
        cond = Attr("shot_type").eq(shot_types[0])
        for st in shot_types[1:]:
            cond = cond | Attr("shot_type").eq(st)
        _and(cond)

    event_types = _csv_param(params, "event_type")
    if event_types:
        cond = Attr("event_type").eq(event_types[0])
        for et in event_types[1:]:
            cond = cond | Attr("event_type").eq(et)
        _and(cond)

    periods = _csv_param(params, "period")
    if periods:
        cond = Attr("period").eq(int(periods[0]))
        for p in periods[1:]:
            cond = cond | Attr("period").eq(int(p))
        _and(cond)

    return filt


def _query_all_pages(table, **kwargs):
    items = []
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp or len(items) >= MAX_ITEMS_RETURNED:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items[:MAX_ITEMS_RETURNED]


def _scan_all_pages(table, **kwargs):
    """Only ever used against NHL_Players (~700-800 rows league-wide),
    never against the much larger play-by-play table."""
    items = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp or len(items) >= MAX_ITEMS_RETURNED:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items[:MAX_ITEMS_RETURNED]


def handle_shots(params):
    game_ids = _csv_param(params, "game_ids")
    team = params.get("team")
    shooter_id = params.get("shooter_id")
    goalie_id = params.get("goalie_id")

    items = []
    # "team" always means the shooting team's tricode. That's unambiguous
    # on its own, but a goalie never faces shots FROM their own team --
    # so once a goalie is selected, "team" can only have been used to
    # narrow the goalie picker (e.g. to that team's own goalies) and must
    # NOT also be applied as a shot filter, or every result would be
    # impossible (shooter team == goalie's own team, which never happens).
    drop_team = {"team_tricode"} if goalie_id else set()

    if game_ids:
        if len(game_ids) > MAX_GAMES_PER_SHOTS_REQUEST:
            return _response(400, {
                "error": f"Too many games requested (max {MAX_GAMES_PER_SHOTS_REQUEST})."
            })

        extra_filter = _build_shot_filters(params, exclude=drop_team)

        def fetch_game(gid):
            kwargs = {"KeyConditionExpression": Key("game_id").eq(int(gid))}
            if extra_filter is not None:
                kwargs["FilterExpression"] = extra_filter
            return _query_all_pages(PBP_TABLE, **kwargs)

        with ThreadPoolExecutor(max_workers=min(10, len(game_ids))) as pool:
            futures = [pool.submit(fetch_game, gid) for gid in game_ids]
            for f in as_completed(futures):
                items.extend(f.result())

    elif shooter_id:
        extra_filter = _build_shot_filters(params, exclude={"shooter_id"} | drop_team)
        kwargs = {
            "IndexName": "shooter-index",
            "KeyConditionExpression": Key("shooter_id").eq(int(shooter_id)),
        }
        if extra_filter is not None:
            kwargs["FilterExpression"] = extra_filter
        items = _query_all_pages(PBP_TABLE, **kwargs)

    elif goalie_id:
        extra_filter = _build_shot_filters(params, exclude={"goalie_id"} | drop_team)
        kwargs = {
            "IndexName": "goalie-index",
            "KeyConditionExpression": Key("goalie_id").eq(int(goalie_id)),
        }
        if extra_filter is not None:
            kwargs["FilterExpression"] = extra_filter
        items = _query_all_pages(PBP_TABLE, **kwargs)

    elif team:
        extra_filter = _build_shot_filters(params, exclude={"team_tricode"})
        kwargs = {
            "IndexName": "team-index",
            "KeyConditionExpression": Key("team_tricode").eq(team),
        }
        if extra_filter is not None:
            kwargs["FilterExpression"] = extra_filter
        items = _query_all_pages(PBP_TABLE, **kwargs)

    else:
        return _response(400, {
            "error": "Provide at least one of: game_ids, team, shooter_id, goalie_id."
        })

    items = items[:MAX_ITEMS_RETURNED]
    return _response(200, {"count": len(items), "shots": items})


def handle_games(params):
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    team = params.get("team")

    kwargs = {
        "IndexName": "date-index",
        "KeyConditionExpression": Key("gsi_type").eq("GAME"),
    }
    if start_date and end_date:
        kwargs["KeyConditionExpression"] &= Key("game_date").between(start_date, end_date)
    elif start_date:
        kwargs["KeyConditionExpression"] &= Key("game_date").gte(start_date)
    elif end_date:
        kwargs["KeyConditionExpression"] &= Key("game_date").lte(end_date)

    if team:
        kwargs["FilterExpression"] = Attr("home_team").eq(team) | Attr("away_team").eq(team)

    kwargs["ScanIndexForward"] = False  # newest first
    items = _query_all_pages(GAMES_TABLE, **kwargs)
    return _response(200, {"count": len(items), "games": items})


def handle_players(params):
    team = params.get("team")
    position = params.get("position")

    if team:
        kwargs = {
            "IndexName": "team-index",
            "KeyConditionExpression": Key("team_tricode").eq(team),
        }
        if position:
            kwargs["FilterExpression"] = Attr("position").eq(position)
        items = _query_all_pages(PLAYERS_TABLE, **kwargs)
    else:
        # No team picked yet: NHL_Players is small league-wide, so an
        # unfiltered scan here is cheap and lets the shooter/goalie
        # pickers work without forcing a team choice first.
        scan_kwargs = {}
        if position:
            scan_kwargs["FilterExpression"] = Attr("position").eq(position)
        items = _scan_all_pages(PLAYERS_TABLE, **scan_kwargs)

    return _response(200, {"count": len(items), "players": items})


def handle_game_players(params):
    """
    Returns the players who actually dressed for the selected game(s),
    read from NHL_GameRosters -- populated at ingest time from the play-
    by-play API's `rosterSpots`, the authoritative source for "who played
    for which team in this specific game." This is deliberately NOT
    reconstructed from NHL_Players (which only reflects *current* rosters
    and drops anyone no longer on an active 32-team roster) or inferred
    from shot events (which only ever tell you the shooter's team, not
    the facing goalie's team).
    """
    game_ids = _csv_param(params, "game_ids")
    team = params.get("team")

    if not game_ids:
        return _response(400, {"error": "game_ids query parameter is required."})
    if len(game_ids) > MAX_GAMES_PER_SHOTS_REQUEST:
        return _response(400, {
            "error": f"Too many games requested (max {MAX_GAMES_PER_SHOTS_REQUEST})."
        })

    def fetch_roster(gid):
        kwargs = {"KeyConditionExpression": Key("game_id").eq(int(gid))}
        if team:
            kwargs["FilterExpression"] = Attr("team_tricode").eq(team)
        return _query_all_pages(GAME_ROSTERS_TABLE, **kwargs)

    players_by_id = {}
    with ThreadPoolExecutor(max_workers=min(10, len(game_ids))) as pool:
        futures = [pool.submit(fetch_roster, gid) for gid in game_ids]
        for f in as_completed(futures):
            for p in f.result():
                players_by_id[p["player_id"]] = p

    players = list(players_by_id.values())
    return _response(200, {"count": len(players), "players": players})


def handle_teams():
    return _response(200, {"teams": TEAMS})


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return _response(200, {})

    raw_path = event.get("rawPath", "") or event.get("requestContext", {}).get("http", {}).get("path", "")
    params = event.get("queryStringParameters") or {}

    try:
        if raw_path.endswith("/shots"):
            return handle_shots(params)
        elif raw_path.endswith("/games"):
            return handle_games(params)
        elif raw_path.endswith("/game_players"):
            return handle_game_players(params)
        elif raw_path.endswith("/players"):
            return handle_players(params)
        elif raw_path.endswith("/teams"):
            return handle_teams()
        else:
            return _response(404, {"error": f"Unknown route: {raw_path}"})
    except Exception as e:
        return _response(500, {"error": str(e)})

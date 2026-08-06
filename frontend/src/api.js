const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:3000";

async function get(path, params = {}) {
  const url = new URL(BASE_URL + path);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });

  const res = await fetch(url.toString());
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function fetchTeams() {
  return get("/teams");
}

export function fetchGames({ startDate, endDate, team } = {}) {
  return get("/games", { start_date: startDate, end_date: endDate, team });
}

export function fetchPlayers({ team, position } = {}) {
  return get("/players", { team, position });
}

export function fetchGamePlayers({ gameIds, team } = {}) {
  return get("/game_players", { game_ids: gameIds?.join(","), team });
}

export function fetchShots(filters) {
  const params = {};
  if (filters.gameIds?.length) params.game_ids = filters.gameIds.join(",");
  if (filters.team) params.team = filters.team;
  if (filters.shooterId) params.shooter_id = filters.shooterId;
  if (filters.goalieId) params.goalie_id = filters.goalieId;
  if (filters.shotTypes?.length) params.shot_type = filters.shotTypes.join(",");
  if (filters.eventTypes?.length) params.event_type = filters.eventTypes.join(",");
  if (filters.periods?.length) params.period = filters.periods.join(",");
  return get("/shots", params);
}

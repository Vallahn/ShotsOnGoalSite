import { useEffect, useMemo, useState } from "react";
import { fetchTeams, fetchShots, fetchPlayers, fetchGamePlayers, fetchGames } from "./api";
import FilterPanel from "./components/FilterPanel";
import RinkChart from "./components/RinkChart";

const SHOT_EVENT_TYPES = ["goal", "shot-on-goal", "missed-shot", "blocked-shot"];

const INITIAL_FILTERS = {
  startDate: "",
  endDate: "",
  selectedGameIds: [],
  team: "",
  shooterId: "",
  goalieId: "",
  shotTypes: [],
  eventTypes: [],
  periods: [],
};

export default function App() {
  const [teams, setTeams] = useState([]);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [shots, setShots] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [normalize, setNormalize] = useState(true);
  const [allPlayers, setAllPlayers] = useState([]);
  const [gamesById, setGamesById] = useState({});

  useEffect(() => {
    fetchTeams().then((data) => setTeams(data.teams || [])).catch(() => setTeams([]));
  }, []);

  const selectedGameIds = filters.selectedGameIds || [];

  useEffect(() => {
    // Full player pool (league-wide, or every player who dressed across
    // the selected games) -- used by both the FilterPanel dropdowns and
    // the shot-tooltip's shooter-name lookup, so it's fetched once here.
    if (selectedGameIds.length > 0) {
      fetchGamePlayers({ gameIds: selectedGameIds })
        .then((data) => setAllPlayers(data.players || []))
        .catch(() => setAllPlayers([]));
    } else {
      fetchPlayers({})
        .then((data) => setAllPlayers(data.players || []))
        .catch(() => setAllPlayers([]));
    }
  }, [JSON.stringify(selectedGameIds)]);

  useEffect(() => {
    // Every game's date, for the shot tooltip -- fetched once since
    // dates don't change.
    fetchGames({})
      .then((data) => {
        const map = {};
        for (const g of data.games || []) {
          map[String(g.game_id)] = g;
        }
        setGamesById(map);
      })
      .catch(() => setGamesById({}));
  }, []);

  const playersById = useMemo(() => {
    const map = {};
    for (const p of allPlayers) {
      map[String(p.player_id)] = p;
    }
    return map;
  }, [allPlayers]);

  const canSearch = Boolean(
    filters.selectedGameIds.length || filters.team || filters.shooterId || filters.goalieId
  );

  const handleApply = async () => {
    if (!canSearch) {
      setError("Pick at least one game, a team, a shooter, or a goalie to search.");
      return;
    }
    setError(null);
    setLoading(true);
    setHasSearched(true);
    try {
      const data = await fetchShots({
        gameIds: filters.selectedGameIds,
        team: filters.team,
        shooterId: filters.shooterId,
        goalieId: filters.goalieId,
        shotTypes: filters.shotTypes,
        eventTypes: filters.eventTypes.length ? filters.eventTypes : SHOT_EVENT_TYPES,
        periods: filters.periods,
      });
      setShots(data.shots || []);
    } catch (e) {
      setError(e.message);
      setShots([]);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setFilters(INITIAL_FILTERS);
    setShots([]);
    setError(null);
    setHasSearched(false);
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__title">
          <span className="app-header__eyebrow">NHL Play-by-Play</span>
          <h1>SHOT MAP</h1>
        </div>
        <div className="app-header__scoreboard">
          <span className="scoreboard-value">{hasSearched ? shots.length : "—"}</span>
          <span className="scoreboard-label">shots shown</span>
        </div>
      </header>

      <main className="app-main">
        <FilterPanel
          teams={teams}
          allPlayers={allPlayers}
          filters={filters}
          onChange={setFilters}
          onApply={handleApply}
          onReset={handleReset}
          loading={loading}
        />

        <section className="app-canvas">
          {error && <div className="banner banner--error">{error}</div>}
          {!hasSearched && !error && (
            <div className="banner banner--hint">
              Pick a team, a shooter, a goalie, or specific games on the left, then hit
              &ldquo;Show shots&rdquo;.
            </div>
          )}
          <label className="normalize-toggle">
            <input
              type="checkbox"
              checked={normalize}
              onChange={(e) => setNormalize(e.target.checked)}
            />
            Normalize shots to one side
          </label>
          <RinkChart shots={shots} normalize={normalize} players={playersById} games={gamesById} />
        </section>
      </main>
    </div>
  );
}

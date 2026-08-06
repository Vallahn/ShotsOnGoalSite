import { useEffect, useMemo, useState } from "react";
import { fetchGames } from "../api";
import SearchableSelect from "./SearchableSelect";

const SHOT_TYPES = [
  "wrist", "slap", "snap", "backhand", "tip-in",
  "deflected", "wrap-around", "poke", "bat", "cradle", "between-legs",
];

const EVENT_TYPES = [
  { value: "goal", label: "Goal" },
  { value: "shot-on-goal", label: "Shot on goal" },
  { value: "missed-shot", label: "Missed" },
  { value: "blocked-shot", label: "Blocked" },
];

const PERIODS = [
  { value: "1", label: "1st" },
  { value: "2", label: "2nd" },
  { value: "3", label: "3rd" },
  { value: "4", label: "OT" },
  { value: "5", label: "SO" },
];

function Chip({ active, onClick, children }) {
  return (
    <button type="button" className={`chip ${active ? "chip--active" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

export default function FilterPanel({ teams, allPlayers, filters, onChange, onApply, onReset, loading }) {
  const [games, setGames] = useState([]);
  const [gamesLoading, setGamesLoading] = useState(false);

  useEffect(() => {
    if (!filters.startDate && !filters.endDate && !filters.team) return;
    setGamesLoading(true);
    fetchGames({ startDate: filters.startDate, endDate: filters.endDate, team: filters.team })
      .then((data) => setGames(data.games || []))
      .catch(() => setGames([]))
      .finally(() => setGamesLoading(false));
  }, [filters.startDate, filters.endDate, filters.team]);

  const selectedGameIds = filters.selectedGameIds || [];

  // Tracks which of shooter/goalie was picked FIRST while a team was
  // selected -- that one is the "anchor" and always shows the team's own
  // players; the other always shows the opposing team, and stays that
  // way even after it's also picked (it must not flip back just because
  // both ended up filled in).
  const [primaryRole, setPrimaryRole] = useState(null); // null | "shooter" | "goalie"

  useEffect(() => {
    // Safety net for external resets (Reset button, manually clearing
    // both dropdowns back to "Any"): once neither is chosen, un-anchor.
    if (!filters.shooterId && !filters.goalieId) {
      setPrimaryRole(null);
    }
  }, [filters.shooterId, filters.goalieId]);

  const set = (patch) => onChange({ ...filters, ...patch });

  const toggleInList = (key, value) => {
    const list = filters[key] || [];
    const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
    set({ [key]: next });
  };

  const toggleGame = (gameId) => {
    const list = filters.selectedGameIds || [];
    const next = list.includes(gameId) ? list.filter((v) => v !== gameId) : [...list, gameId];
    set({ selectedGameIds: next });
  };

  const skaterPool = useMemo(() => (allPlayers || []).filter((p) => p.position !== "G"), [allPlayers]);
  const goaliePool = useMemo(() => (allPlayers || []).filter((p) => p.position === "G"), [allPlayers]);

  // No team chosen: show everyone. Team chosen: whichever role was
  // picked FIRST stays anchored to the team's own players; the other
  // role shows opposing-team players, whether or not it's been picked
  // yet, and stays that way once it is.
  const shooters = !filters.team
    ? skaterPool
    : primaryRole === "goalie"
    ? skaterPool.filter((p) => p.team_tricode !== filters.team)
    : skaterPool.filter((p) => p.team_tricode === filters.team);

  const goalies = !filters.team
    ? goaliePool
    : primaryRole === "shooter"
    ? goaliePool.filter((p) => p.team_tricode !== filters.team)
    : goaliePool.filter((p) => p.team_tricode === filters.team);

  const teamOptions = useMemo(() => teams.map((t) => ({ value: t, label: t })), [teams]);
  const shooterOptions = useMemo(
    () => shooters.map((p) => ({ value: p.player_id, label: `${p.first_name} ${p.last_name} (${p.team_tricode} · ${p.position})` })),
    [shooters]
  );
  const goalieOptions = useMemo(
    () => goalies.map((p) => ({ value: p.player_id, label: `${p.first_name} ${p.last_name} (${p.team_tricode})` })),
    [goalies]
  );

  return (
    <aside className="filter-panel">
      <div className="filter-panel__header">
        <span className="eyebrow">Filters</span>
        <div className="filter-panel__header-row">
          <h2>Narrow the ice</h2>
          <button type="button" className="reset-btn" onClick={onReset}>
            Reset
          </button>
        </div>
      </div>

      <section className="filter-group">
        <label className="filter-label">Date range</label>
        <div className="filter-row">
          <input
            type="date"
            value={filters.startDate || ""}
            onChange={(e) => set({ startDate: e.target.value })}
          />
          <span className="filter-row__sep">to</span>
          <input
            type="date"
            value={filters.endDate || ""}
            onChange={(e) => set({ endDate: e.target.value })}
          />
        </div>
      </section>

      <section className="filter-group">
        <label className="filter-label">Team</label>
        <SearchableSelect
          options={teamOptions}
          value={filters.team || ""}
          placeholder="Any team"
          onChange={(value) => {
            set({ team: value, shooterId: "", goalieId: "" });
            setPrimaryRole(null);
          }}
        />
      </section>

      {games.length > 0 && (
        <section className="filter-group">
          <label className="filter-label">
            Games {gamesLoading && <span className="filter-label__hint">loading…</span>}
          </label>
          <div className="game-list">
            {games.map((g) => (
              <label key={g.game_id} className="game-list__item">
                <input
                  type="checkbox"
                  checked={(filters.selectedGameIds || []).includes(g.game_id)}
                  onChange={() => toggleGame(g.game_id)}
                />
                <span>{g.game_date} · {g.away_team} @ {g.home_team}</span>
              </label>
            ))}
          </div>
        </section>
      )}

      <section className="filter-group">
        <label className="filter-label">Shooter</label>
        <SearchableSelect
          options={shooterOptions}
          value={filters.shooterId || ""}
          placeholder="Any shooter"
          onChange={(value) => {
            set({ shooterId: value });
            if (value) setPrimaryRole((prev) => prev || "shooter");
          }}
        />
      </section>

      <section className="filter-group">
        <label className="filter-label">Goalie</label>
        <SearchableSelect
          options={goalieOptions}
          value={filters.goalieId || ""}
          placeholder="Any goalie"
          onChange={(value) => {
            set({ goalieId: value });
            if (value) setPrimaryRole((prev) => prev || "goalie");
          }}
        />
      </section>

      <section className="filter-group">
        <label className="filter-label">Shot type</label>
        <div className="chip-row">
          {SHOT_TYPES.map((st) => (
            <Chip key={st} active={(filters.shotTypes || []).includes(st)} onClick={() => toggleInList("shotTypes", st)}>
              {st}
            </Chip>
          ))}
        </div>
      </section>

      <section className="filter-group">
        <label className="filter-label">Result</label>
        <div className="chip-row">
          {EVENT_TYPES.map((et) => (
            <Chip key={et.value} active={(filters.eventTypes || []).includes(et.value)} onClick={() => toggleInList("eventTypes", et.value)}>
              {et.label}
            </Chip>
          ))}
        </div>
      </section>

      <section className="filter-group">
        <label className="filter-label">Period</label>
        <div className="chip-row">
          {PERIODS.map((p) => (
            <Chip key={p.value} active={(filters.periods || []).includes(p.value)} onClick={() => toggleInList("periods", p.value)}>
              {p.label}
            </Chip>
          ))}
        </div>
      </section>

      <button type="button" className="apply-btn" onClick={onApply} disabled={loading}>
        {loading ? "Loading…" : "Show shots"}
      </button>
    </aside>
  );
}

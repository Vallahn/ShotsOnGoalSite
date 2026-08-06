import { useMemo, useState } from "react";

// Coordinates below are in feet, matching the NHL API's x_coord/y_coord
// system directly: center ice is (0,0), goal lines sit at x = ±89, and
// the boards run y = ±42.5. Using feet as the SVG viewBox unit means no
// manual scaling math is needed anywhere else in this component.

const EVENT_STYLES = {
  goal: { label: "Goal", fill: "var(--amber)", stroke: "var(--frost)", r: 1.6 },
  "shot-on-goal": { label: "Shot on goal", fill: "rgba(30,77,140,0.55)", stroke: "var(--rink-blue)", r: 1.2 },
  "missed-shot": { label: "Missed", fill: "transparent", stroke: "var(--steel)", r: 1.2 },
  "blocked-shot": { label: "Blocked", fill: "var(--rink-red)", stroke: "var(--rink-red)", r: 1.0 },
};

function FaceoffCircle({ cx, cy }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={15} className="rink-line rink-line--red" />
      <circle cx={cx} cy={cy} r={1} className="rink-fill rink-fill--red" />
    </g>
  );
}

function Crease({ side }) {
  // side: -1 for left goal (x=-89), +1 for right goal (x=89)
  const x = 89 * side;
  const sweep = side === -1 ? 1 : 0;
  return (
    <path
      d={`M ${x} -4 A 4.5 4.5 0 0 ${sweep} ${x} 4`}
      className="rink-fill rink-fill--blue-soft"
    />
  );
}

function Rink() {
  return (
    <g>
      {/* Boards */}
      <rect x={-100} y={-42.5} width={200} height={85} rx={28} ry={28} className="rink-boards" />

      {/* Center line */}
      <line x1={0} y1={-42.5} x2={0} y2={42.5} className="rink-line rink-line--red-thick" />
      {/* Blue lines */}
      <line x1={-25} y1={-42.5} x2={-25} y2={42.5} className="rink-line rink-line--blue-thick" />
      <line x1={25} y1={-42.5} x2={25} y2={42.5} className="rink-line rink-line--blue-thick" />
      {/* Goal lines */}
      <line x1={-89} y1={-42.5} x2={-89} y2={42.5} className="rink-line rink-line--red-thin" />
      <line x1={89} y1={-42.5} x2={89} y2={42.5} className="rink-line rink-line--red-thin" />

      {/* Center circle + dot */}
      <circle cx={0} cy={0} r={15} className="rink-line rink-line--blue" />
      <circle cx={0} cy={0} r={0.6} className="rink-fill rink-fill--blue" />

      {/* Zone faceoff circles */}
      <FaceoffCircle cx={69} cy={22} />
      <FaceoffCircle cx={69} cy={-22} />
      <FaceoffCircle cx={-69} cy={22} />
      <FaceoffCircle cx={-69} cy={-22} />

      {/* Neutral zone dots */}
      <circle cx={20} cy={22} r={0.6} className="rink-fill rink-fill--red" />
      <circle cx={20} cy={-22} r={0.6} className="rink-fill rink-fill--red" />
      <circle cx={-20} cy={22} r={0.6} className="rink-fill rink-fill--red" />
      <circle cx={-20} cy={-22} r={0.6} className="rink-fill rink-fill--red" />

      {/* Creases */}
      <Crease side={-1} />
      <Crease side={1} />

      {/* Goal mouths */}
      <rect x={-91.5} y={-3} width={2.5} height={6} className="rink-goal" />
      <rect x={89} y={-3} width={2.5} height={6} className="rink-goal" />
    </g>
  );
}

export default function RinkChart({ shots, normalize = false, players = {}, games = {} }) {
  const [hovered, setHovered] = useState(null);

  const points = useMemo(() => {
    return shots
      .filter((s) => Object.prototype.hasOwnProperty.call(EVENT_STYLES, s.event_type))
      .map((s) => {
        let x, y;
        if (normalize && s.norm_x !== undefined && s.norm_x !== "") {
          x = parseFloat(s.norm_x);
          y = parseFloat(s.norm_y);
        } else {
          x = parseFloat(s.x_coord);
          y = parseFloat(s.y_coord);
        }
        if (Number.isNaN(x) || Number.isNaN(y)) return null;
        const style = EVENT_STYLES[s.event_type];
        return { ...s, x, y, style };
      })
      .filter(Boolean);
  }, [shots, normalize]);

  return (
    <div className="rink-wrap">
      <svg viewBox="-105 -50 210 100" className="rink-svg" role="img" aria-label="NHL rink shot map">
        <Rink />
        {points.map((p) => (
          <circle
            key={`${p.game_id}-${p.event_id}`}
            cx={p.x}
            cy={p.y}
            r={p.style.r}
            fill={p.style.fill}
            stroke={p.style.stroke}
            strokeWidth={0.35}
            className="shot-dot"
            onMouseEnter={() => setHovered(p)}
            onMouseLeave={() => setHovered(null)}
          />
        ))}
      </svg>

      {hovered && (
        <div className="rink-tooltip">
          <strong>{EVENT_STYLES[hovered.event_type]?.label}</strong>
          {(() => {
            const shooter = players[String(hovered.shooter_id)];
            const name = shooter ? `${shooter.first_name} ${shooter.last_name}` : null;
            return name ? <span>{name}</span> : null;
          })()}
          <span>{hovered.shot_type || "unknown type"} · P{hovered.period} · {hovered.time_in_period}</span>
          <span>{games[String(hovered.game_id)]?.game_date || `Game ${hovered.game_id}`}</span>
        </div>
      )}

      <div className="rink-legend">
        {Object.entries(EVENT_STYLES).map(([key, s]) => (
          <div className="rink-legend__item" key={key}>
            <span
              className="rink-legend__swatch"
              style={{ background: s.fill === "transparent" ? "transparent" : s.fill, borderColor: s.stroke }}
            />
            {s.label}
          </div>
        ))}
      </div>
    </div>
  );
}

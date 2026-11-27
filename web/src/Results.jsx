import { addDays } from "./tripForm.js";

const LABELS = {
  lowest_total: "Lowest total",
  least_time: "Least travel time",
  fairest: "Fairest split",
  baseline: "For comparison only: everyone flies to the nearest airport",
};

const usd = (x) => "$" + Math.round(x).toLocaleString();
const signed = (x) => (x > 0 ? "+" : x < 0 ? "−" : "") + usd(Math.abs(x));

function fmtDay(iso) {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

const MODE_TAG = { detour: "split · one car picks up", second_car: "split · two car groups" };

function who(members, total) {
  return members.length === total ? "Everyone" : members.join(", ");
}

function cars(n) {
  return `${n} car${n === 1 ? "" : "s"}`;
}

function arrivalLines(plan) {
  const total = plan.per_member.length;
  if (!plan.arrivals) return [`Everyone flies into ${plan.gateway_in}, picks up the car`];
  return plan.arrivals.map((tr) => {
    const people = who(tr.members, total);
    const one = people === "Everyone" || tr.members.length === 1;
    const fly = `${people} ${one ? "flies" : "fly"} into ${tr.airport}`;
    if (tr.cars === 0) return `${fly} and ${one ? "gets" : "get"} picked up there`;
    return `${fly}, ${one ? "picks" : "pick"} up ${cars(tr.cars)}, drive ${tr.path.join(" → ")} (${tr.drive_h.toFixed(1)}h)`;
  });
}

function departureLines(plan, last) {
  const total = plan.per_member.length;
  if (!plan.departures) {
    return [`Drive ${last} → ${plan.gateway_out}, drop off the car, everyone flies home from ${plan.gateway_out}`];
  }
  return plan.departures.map((tr) => {
    const people = who(tr.members, total);
    const one = people === "Everyone" || tr.members.length === 1;
    const flyHome = `${people} ${one ? "flies" : "fly"} home from ${tr.airport}`;
    if (tr.cars === 0) return `Drop-off at ${tr.airport} on the way; ${flyHome}`;
    return `Drive ${tr.path.join(" → ")} (${tr.drive_h.toFixed(1)}h), return ${cars(tr.cars)}; ${flyHome}`;
  });
}

function itinerary(plan) {
  const days = [];
  const drives = plan.drive_hours_by_day;
  let day = plan.start_date;
  plan.route.forEach((stop, i) => {
    const lines = [];
    if (i === 0) lines.push(...arrivalLines(plan));
    else lines.push(`Drive ${plan.route[i - 1].place} → ${stop.place} (${drives[i].toFixed(1)}h)`);
    days.push({ date: day, lines, stay: stop.place });
    for (let n = 1; n < stop.nights; n++) {
      days.push({ date: addDays(day, n), lines: [`Free day in ${stop.place}`], stay: stop.place });
    }
    day = addDays(day, stop.nights);
  });
  const last = plan.route[plan.route.length - 1].place;
  days.push({ date: day, lines: departureLines(plan, last), stay: null });
  return days;
}

function PlanCard({ plan, baseline, index, warnings = [], stays = {} }) {
  const t = plan.totals;
  const openJaw = plan.gateway_in !== plan.gateway_out;
  const isBaseline = plan.label === "baseline";
  const dT = baseline && !isBaseline ? t.transport - baseline.totals.transport : null;
  const dH = baseline && !isBaseline ? plan.transit_hours - baseline.transit_hours : null;

  return (
    <div className={`card plan ${isBaseline ? "baseline" : ""}`}>
      <div className="plan-head">
        <div>
          <div className="plan-label">
            {index ? `#${index} ` : ""}
            {LABELS[plan.label] || plan.label}
          </div>
          <h3>
            Fly into <b>{plan.gateway_in}</b>
            {plan.pickup && (
              <>
                {" "}
                + <b>{plan.pickup}</b>
              </>
            )}
            , out of <b>{plan.gateway_out}</b>
            {plan.dropoff && (
              <>
                {" "}
                + <b>{plan.dropoff}</b>
              </>
            )}
            <span className="tag">{MODE_TAG[plan.mode] || (openJaw ? "open-jaw" : "round trip")}</span>
          </h3>
          <div className="muted">
            {fmtDay(plan.start_date)} → {fmtDay(plan.end_date)}
          </div>
        </div>
        <div className="plan-total">
          <div className="big">{usd(t.money)}</div>
          <div className="muted">{usd(t.money / plan.per_member.length)}/person avg</div>
          {dT !== null && (
            <div className={dT < 0 ? "good" : "bad"}>
              transport {signed(dT)} ({((dT / baseline.totals.transport) * 100).toFixed(1)}%) vs naive
            </div>
          )}
        </div>
      </div>

      {isBaseline && (
        <div className="comparison-note">
          Not a recommendation. This is the plan a group would make without the optimizer, used to measure savings.
          {warnings.length > 0 && <> It isn't allowed under your rules: {warnings.join(" ")}</>}
        </div>
      )}

      <div className="breakdown">
        <span>Flights {usd(t.flights)}</span>
        <span>
          Car {usd(t.car_rental)}
          {plan.n_cars ? ` (${cars(plan.n_cars)})` : ""}
        </span>
        <span>Drop fee {usd(t.drop_fee)}</span>
        <span>Fuel {usd(t.fuel)}</span>
        <span>Lodging {usd(t.lodging)}</span>
        {t.penalty > 0 && <span className="bad">Drive penalty {usd(t.penalty)}</span>}
        <span>
          {Math.round(plan.transit_hours)} travel person-hours
          {dH !== null && ` (${dH > 0 ? "+" : ""}${Math.round(dH)})`}
        </span>
      </div>

      <details>
        <summary>Day-by-day itinerary</summary>
        <ol className="itinerary">
          {itinerary(plan).map((d, i) => (
            <li key={i}>
              <span className="day">
                Day {i + 1} · {fmtDay(d.date)}
              </span>
              <span>
                {d.lines.join(" · ")}
                {d.stay && (
                  <span className="muted">
                    {" "}
                    — night in {d.stay}
                    {stays[d.stay] ? ` at ${stays[d.stay]}` : ""}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ol>
      </details>

      <table>
        <thead>
          <tr>
            <th>Traveler</th>
            <th>Outbound</th>
            <th>Return</th>
            <th className="num">Pays</th>
            <th className="num">Travel h</th>
          </tr>
        </thead>
        <tbody>
          {plan.per_member.map((m) => (
            <tr key={m.name} className={m.over_budget ? "over" : ""}>
              <td>{m.name}</td>
              {m.round_trip ? (
                <td colSpan={2}>
                  {m.out.from}⇄{m.out.to} round-trip ticket {usd(m.out.price + m.ret.price)}
                </td>
              ) : (
                <>
                  <td>
                    {m.out.from}→{m.out.to} {usd(m.out.price)}
                  </td>
                  <td>
                    {m.ret.from}→{m.ret.to} {usd(m.ret.price)}
                  </td>
                </>
              )}
              <td className="num">
                {usd(m.share)}
                {m.over_budget && <span className="tag bad">over budget</span>}
              </td>
              <td className="num">{m.hours.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">Gap between highest and lowest payer: {usd(plan.share_gap)}</div>
    </div>
  );
}

export default function Results({ run, stays = {} }) {
  const r = run.result;
  const rejected = Object.entries(r.stats).filter(([k]) => k.startsWith("rejected"));
  return (
    <div className="results">
      {r.synthetic_fares && (
        <div className="warn">
          ⚠ Synthetic fares: these prices are made up from distance, so they only show how the optimizer behaves.
          Don't make real decisions from them.
        </div>
      )}

      <div className="card summary">
        {r.plans.length > 0 && r.transport_savings_pct !== null ? (
          <div className="big">
            Saves {usd(r.transport_savings_usd)} on flights + car ({r.transport_savings_pct.toFixed(1)}%) vs everyone
            flying to {r.baseline_airport}
          </div>
        ) : r.plans.length === 0 ? (
          <div>
            <div className="big bad">No plan works with these inputs. Here's why:</div>
            <ul className="reasons">
              {(r.why_no_plan?.length ? r.why_no_plan : ["Try loosening budgets, traveler dates or the daily drive limit."]).map(
                (m, i) => (
                  <li key={i}>{m}</li>
                )
              )}
            </ul>
          </div>
        ) : (
          <div className="big">No fares found for the naive plan at {r.baseline_airport}, so there's nothing to compare against.</div>
        )}
        <div className="muted small">
          Checked {(r.stats.evaluated || 0).toLocaleString()} combinations, {r.feasible_count.toLocaleString()} feasible
          {rejected.length > 0 &&
            " · ruled out: " + rejected.map(([k, v]) => `${k.replace("rejected: ", "")} ${v}`).join(", ")}
          {" · "}fares: {r.fare_source} · run {new Date(run.created_at + "Z").toLocaleString()}
        </div>
        <details>
          <summary>Airports considered</summary>
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>Airport</th>
                <th className="num">Drive h</th>
                <th>Nearest place</th>
                <th className="num">Score</th>
                <th>Used</th>
              </tr>
            </thead>
            <tbody>
              {r.gateway_details.map((g) => (
                <tr key={g.code}>
                  <td>{g.code}</td>
                  <td>{g.name}</td>
                  <td className="num">{g.drive_h.toFixed(1)}</td>
                  <td>{g.nearest_place}</td>
                  <td className="num">{g.score === null ? "no fares" : usd(g.score)}</td>
                  <td>{g.kept ? "✓" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </div>

      {r.plans.map((p, i) => (
        <PlanCard key={i} plan={p} baseline={r.baseline} index={i + 1} stays={stays} />
      ))}
      {r.baseline && <PlanCard plan={r.baseline} baseline={null} warnings={r.baseline_warnings || []} stays={stays} />}
    </div>
  );
}

import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { ComparisonRow } from "../lib/types";
import { Badge, Card, Empty, Notice, num, signed, statusKind } from "../components/common";

/**
 * Scenarios and sensitivity.
 *
 * A single optimum is a fragile thing to hand someone. What matters is how it
 * moves: which scenarios break it, and which parameters it is actually
 * sensitive to. Both comparisons run through one code path so a difference
 * between columns is a difference in the data, not in the run.
 */
export default function ScenarioPage({
  problemKey,
  scenarios,
}: {
  problemKey: string | null;
  scenarios: string[];
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [rows, setRows] = useState<ComparisonRow[] | null>(null);
  const [sensitivity, setSensitivity] = useState<{
    method: string;
    objective_name: string | null;
    baseline_objective: number | null;
    ranking: Array<Record<string, unknown>>;
    notes: string[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    setChosen(scenarios);
    setRows(null);
    setSensitivity(null);
  }, [problemKey, scenarios.join(",")]);

  async function compare() {
    if (!problemKey) return;
    setBusy("compare");
    setError(null);
    try {
      const body = await api.compare(problemKey, [null, ...chosen]);
      setRows(body.comparison);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  }

  async function runSensitivity(method: string) {
    if (!problemKey) return;
    setBusy(method);
    setError(null);
    try {
      setSensitivity(
        await api.sensitivity(problemKey, {
          method,
          multipliers: [0.9, 1.1],
          max_solves: 20,
        }),
      );
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  }

  if (!problemKey) return <Empty>Select a problem first.</Empty>;

  const maxInfluence = Math.max(
    1,
    ...(sensitivity?.ranking ?? []).map((r) =>
      Math.abs(Number(r.max_absolute_change ?? r.marginal_effect ?? 0)),
    ),
  );

  return (
    <>
      {error && <Notice kind="bad">{error}</Notice>}

      <Card
        title="Compare scenarios"
        subtitle="The same problem under different assumptions, solved identically."
        actions={
          <button className="primary" onClick={compare} disabled={busy !== null}>
            {busy === "compare" ? "Solving…" : "Compare"}
          </button>
        }
      >
        {scenarios.length === 0 ? (
          <Empty>This problem defines no scenarios.</Empty>
        ) : (
          <div className="row" style={{ marginBottom: "0.75rem" }}>
            {scenarios.map((s) => (
              <label key={s} className="row" style={{ gap: "0.3rem" }}>
                <input
                  type="checkbox"
                  checked={chosen.includes(s)}
                  onChange={(e) =>
                    setChosen((current) =>
                      e.target.checked ? [...current, s] : current.filter((x) => x !== s),
                    )
                  }
                />
                {s}
              </label>
            ))}
          </div>
        )}

        {rows && (
          <table>
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Status</th>
                <th className="num">Objective</th>
                <th className="num">vs baseline</th>
                <th className="num">Decisions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.scenario}>
                  <td>
                    {row.scenario}
                    {row.error && <div className="muted">{row.error}</div>}
                    {row.warnings?.map((w) => (
                      <div key={w} className="muted">
                        {w}
                      </div>
                    ))}
                  </td>
                  <td>
                    <Badge kind={statusKind(row.status)}>{row.status}</Badge>
                  </td>
                  <td className="num">
                    {row.objectives.length > 0 ? num(row.objectives[0].value) : "—"}
                  </td>
                  <td className="num">
                    {row.scenario === "baseline" ? (
                      <span className="muted">—</span>
                    ) : (
                      signed(row.delta_vs_baseline)
                    )}
                  </td>
                  <td className="num">{row.decisions ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card
        title="Sensitivity"
        subtitle="Which numbers actually move the answer."
        actions={
          <>
            <button
              className="secondary"
              onClick={() => runSensitivity("shadow_prices")}
              disabled={busy !== null}
            >
              {busy === "shadow_prices" ? "Working…" : "Shadow prices"}
            </button>
            <button
              className="secondary"
              onClick={() => runSensitivity("one_at_a_time")}
              disabled={busy !== null}
            >
              {busy === "one_at_a_time" ? "Re-solving…" : "Perturb and re-solve"}
            </button>
          </>
        }
      >
        <p className="muted">
          Shadow prices are exact but only exist for continuous models. Perturb-and-re-solve is
          slower and works on anything, including integer models where no dual exists.
        </p>

        {!sensitivity && <Empty>Run one of the two methods.</Empty>}

        {sensitivity && (
          <>
            {sensitivity.notes.map((note) => (
              <Notice key={note} kind="warn">
                {note}
              </Notice>
            ))}
            {sensitivity.baseline_objective !== null && (
              <p className="muted">
                Baseline {sensitivity.objective_name}: {num(sensitivity.baseline_objective)}
              </p>
            )}
            {sensitivity.ranking.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>{sensitivity.method === "shadow_prices" ? "Constraint" : "Parameter"}</th>
                    <th className="num">Influence</th>
                    <th style={{ width: "40%" }} />
                  </tr>
                </thead>
                <tbody>
                  {sensitivity.ranking.map((entry, index) => {
                    const magnitude = Math.abs(
                      Number(entry.max_absolute_change ?? entry.marginal_effect ?? 0),
                    );
                    return (
                      <tr key={index}>
                        <td className="mono">
                          {String(entry.parameter ?? entry.constraint ?? "")}
                          {entry.statement ? (
                            <div className="muted">{String(entry.statement)}</div>
                          ) : null}
                        </td>
                        <td className="num">{num(magnitude)}</td>
                        <td>
                          <div className="bar">
                            <span style={{ width: `${(magnitude / maxInfluence) * 100}%` }} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </>
        )}
      </Card>
    </>
  );
}

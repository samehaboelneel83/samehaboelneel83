import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import type { CompileResponse, SolverDescription } from "../lib/types";
import { Badge, Card, Empty, Notice, Stat, num } from "../components/common";

/**
 * Compile without solving.
 *
 * This screen exists because "it compiled" and "it is the model you meant" are
 * different claims. It shows the size of the generated system, which engines
 * are eligible for it and why, and the mapping from every generated element
 * back to the problem element that produced it.
 */
export default function ModelPage({
  problemKey,
  scenarios,
}: {
  problemKey: string | null;
  scenarios: string[];
}) {
  const [scenario, setScenario] = useState<string>("");
  const [compiled, setCompiled] = useState<CompileResponse | null>(null);
  const [solvers, setSolvers] = useState<SolverDescription[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [where, setWhere] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.solvers().then((body) => setSolvers(body.solvers)).catch(() => undefined);
  }, []);

  useEffect(() => {
    setCompiled(null);
    setError(null);
    setScenario("");
  }, [problemKey]);

  async function compile() {
    if (!problemKey) return;
    setBusy(true);
    setError(null);
    setWhere(null);
    try {
      setCompiled(await api.compile(problemKey, scenario || null));
    } catch (e) {
      setCompiled(null);
      setError(String((e as Error).message));
      if (e instanceof ApiError) {
        const detail = e.detail as Record<string, unknown> | null;
        setWhere(typeof detail?.where === "string" ? detail.where : null);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!problemKey) {
    return <Empty>Select a problem first.</Empty>;
  }

  return (
    <>
      <Card
        title="Compile"
        subtitle="Problem Model → Model IR → flat linear system. Nothing is solved here."
        actions={
          <>
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              <option value="">baseline</option>
              {scenarios.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button className="primary" onClick={compile} disabled={busy}>
              {busy ? "Compiling…" : "Compile"}
            </button>
          </>
        }
      >
        {error && (
          <Notice kind="bad">
            {error}
            {where && (
              <div className="muted" style={{ marginTop: "0.25rem" }}>
                The compiler located this in {where} — the problem model needs fixing there, not
                the solver.
              </div>
            )}
          </Notice>
        )}

        {!compiled && !error && <Empty>Compile to inspect the generated model.</Empty>}

        {compiled && (
          <>
            {compiled.record.warnings.map((w) => (
              <Notice key={w} kind="warn">
                {w}
              </Notice>
            ))}

            <div className="grid three">
              <Stat label="Columns" value={num(compiled.statistics.variables)} note="decision variables" />
              <Stat label="Rows" value={num(compiled.statistics.constraints)} note="constraint instances" />
              <Stat label="Non-zeros" value={num(compiled.statistics.nonzeros)} note="matrix density" />
              <Stat
                label="Kind"
                value={compiled.statistics.is_integer ? "Discrete" : "Continuous"}
                note={Object.entries(compiled.statistics.variable_kinds)
                  .map(([k, v]) => `${v} ${k}`)
                  .join(", ")}
              />
              <Stat
                label="Structure"
                value={compiled.structure ?? "general"}
                note={
                  compiled.structure
                    ? "a specialised engine can exploit this"
                    : "no special structure recognised"
                }
              />
              <Stat
                label="Fingerprint"
                value={<span className="mono" style={{ fontSize: "0.9rem" }}>{compiled.fingerprint.slice(0, 12)}</span>}
                note="identical models share this"
              />
            </div>
          </>
        )}
      </Card>

      {compiled && (
        <div className="grid two">
          <Card
            title="Eligible engines"
            subtitle="Capability-driven and decided before any engine is loaded."
          >
            <table>
              <thead>
                <tr>
                  <th>Engine</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {solvers.map((s) => {
                  const verdict = eligibility(s, compiled);
                  return (
                    <tr key={s.name}>
                      <td className="mono">{s.name}</td>
                      <td>
                        <Badge kind={verdict.ok ? "good" : "neutral"}>
                          {verdict.ok ? "eligible" : "not eligible"}
                        </Badge>
                        {verdict.reason && <div className="muted">{verdict.reason}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>

          {compiled.record.applied_overrides.length > 0 && (
            <Card
              title="Scenario overrides applied"
              subtitle="Every value this scenario changed, with what it was before."
            >
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Parameter</th>
                      <th className="num">Baseline</th>
                      <th className="num">Used</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compiled.record.applied_overrides.map((o, index) => (
                      <tr key={`${o.parameter}-${index}`}>
                        <td className="mono">
                          {o.parameter}
                          {o.index.length > 0 && `[${o.index.join(", ")}]`}
                        </td>
                        <td className="num">{num(o.baseline)}</td>
                        <td className="num">{num(o.value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}

      {compiled && (
        <Card
          title="Generated rows"
          subtitle="Each row keeps the statement of the constraint family it came from."
        >
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Row</th>
                  <th>From</th>
                  <th className="num">Terms</th>
                  <th>Relation</th>
                  <th>Means</th>
                </tr>
              </thead>
              <tbody>
                {compiled.constraint_rows.map((row) => (
                  <tr key={row.key}>
                    <td className="mono" title={row.key}>{row.label ?? row.key}</td>
                    <td className="mono muted">{row.name}</td>
                    <td className="num">{row.terms}</td>
                    <td className="mono">
                      {row.op === "le" ? "≤" : row.op === "ge" ? "≥" : "="} {num(row.rhs)}
                    </td>
                    <td className="muted">{row.statement}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

/** Mirrors the server's capability check so the UI can explain eligibility
 *  without a round trip per engine. The server remains the authority. */
function eligibility(
  solver: SolverDescription,
  compiled: CompileResponse,
): { ok: boolean; reason: string | null } {
  const caps = solver.capabilities;
  const kinds = compiled.statistics.variable_kinds;
  if ((kinds.continuous ?? 0) > 0 && caps.continuous === false) {
    return { ok: false, reason: "needs a fully discrete model" };
  }
  if ((kinds.integer ?? 0) > 0 && caps.integer === false) {
    return { ok: false, reason: "cannot take integer variables" };
  }
  if ((kinds.binary ?? 0) > 0 && caps.binary === false) {
    return { ok: false, reason: "cannot take binary variables" };
  }
  if (caps.requires_structure && caps.requires_structure !== compiled.structure) {
    return {
      ok: false,
      reason: `requires ${String(caps.requires_structure)} structure`,
    };
  }
  return { ok: true, reason: caps.duals ? "provides shadow prices" : null };
}

import { useState } from "react";

import { ApiError, api, solveStreaming } from "../lib/api";
import type { Diagnosis, SolveResponse, Solution } from "../lib/types";
import { Badge, Card, Empty, Notice, Stat, num, statusKind } from "../components/common";

interface Stage {
  name: string;
  detail: string;
  state: "done" | "pending" | "failed";
}

const STAGE_LABEL: Record<string, string> = {
  compiling: "Compiling the problem model into the IR",
  compiled: "Model compiled",
  solving: "Handing the flat model to a solver process",
  solved: "Solver finished",
  complete: "Run recorded",
};

export default function SolvePage({
  problemKey,
  scenarios,
  onSolved,
}: {
  problemKey: string | null;
  scenarios: string[];
  onSolved: (response: {
    solutionId: string | null;
    solution: Solution;
    diagnosis: Diagnosis | null;
  }) => void;
}) {
  const [scenario, setScenario] = useState("");
  const [solver, setSolver] = useState("");
  const [timeLimit, setTimeLimit] = useState(30);
  const [stages, setStages] = useState<Stage[]>([]);
  const [solution, setSolution] = useState<Solution | null>(null);
  const [selection, setSelection] = useState<SolveResponse["selection"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [considered, setConsidered] = useState<
    Array<{ solver: string; eligible: boolean; reason: string | null }>
  >([]);
  const [busy, setBusy] = useState(false);

  async function solveOverSocket() {
    if (!problemKey) return;
    setBusy(true);
    setStages([]);
    setSolution(null);
    setSelection(null);
    setError(null);
    setConsidered([]);
    try {
      const final = await solveStreaming(
        problemKey,
        { scenario: scenario || null, solver: solver || null, timeLimit },
        (message) => {
          setStages((current) => [
            ...current,
            {
              name: STAGE_LABEL[message.stage] ?? message.stage,
              detail: describeStage(message),
              state: message.stage === "error" ? "failed" : "done",
            },
          ]);
        },
      );
      const result = final.solution as Solution;
      setSolution(result);
      onSolved({
        solutionId: (final.solution_id as string) ?? null,
        solution: result,
        diagnosis: (final.diagnosis as Diagnosis | null) ?? null,
      });
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function solveOverHttp() {
    if (!problemKey) return;
    setBusy(true);
    setStages([]);
    setSolution(null);
    setError(null);
    setConsidered([]);
    try {
      const response = await api.solve(problemKey, {
        scenario: scenario || null,
        solver: solver || null,
        time_limit_seconds: timeLimit,
      });
      setSolution(response.solution);
      setSelection(response.selection);
      onSolved({
        solutionId: response.solution_id,
        solution: response.solution,
        diagnosis: response.diagnosis ?? null,
      });
    } catch (e) {
      setError(String((e as Error).message));
      if (e instanceof ApiError) setConsidered(e.considered);
    } finally {
      setBusy(false);
    }
  }

  if (!problemKey) return <Empty>Select a problem first.</Empty>;

  return (
    <>
      <Card
        title="Solve"
        subtitle="The engine is chosen from the model's capabilities. Naming one makes it mandatory, not preferred."
      >
        <div className="row">
          <label className="field">
            Scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              <option value="">baseline</option>
              {scenarios.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Engine
            <select value={solver} onChange={(e) => setSolver(e.target.value)}>
              <option value="">choose automatically</option>
              <option value="highs">highs</option>
              <option value="cpsat">cpsat</option>
              <option value="networkx">networkx</option>
            </select>
          </label>
          <label className="field">
            Time limit (s)
            <input
              type="number"
              min={1}
              max={600}
              value={timeLimit}
              onChange={(e) => setTimeLimit(Number(e.target.value))}
              style={{ width: "6rem" }}
            />
          </label>
          <div className="row" style={{ alignSelf: "flex-end" }}>
            <button className="primary" onClick={solveOverSocket} disabled={busy}>
              {busy ? "Running…" : "Solve (streamed)"}
            </button>
            <button className="secondary" onClick={solveOverHttp} disabled={busy}>
              Solve
            </button>
          </div>
        </div>

        {error && (
          <div style={{ marginTop: "0.75rem" }}>
            <Notice kind="bad">{error}</Notice>
            {considered.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Engine</th>
                    <th>Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {considered.map((c) => (
                    <tr key={c.solver}>
                      <td className="mono">{c.solver}</td>
                      <td>
                        <Badge kind={c.eligible ? "good" : "neutral"}>
                          {c.eligible ? "would accept this model" : "not eligible"}
                        </Badge>
                        {c.reason && <div className="muted">{c.reason}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {stages.length > 0 && (
          <div className="stages" style={{ marginTop: "0.9rem" }}>
            {stages.map((stage, index) => (
              <div key={index} className={`stage ${stage.state}`}>
                <span className="dot" />
                <span>{stage.name}</span>
                <span className="muted">{stage.detail}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {solution && (
        <Card
          title="Outcome"
          actions={
            <>
              <Badge kind={statusKind(solution.status)}>{solution.status}</Badge>
              <Badge kind="accent">{solution.solver}</Badge>
            </>
          }
        >
          {solution.warnings.map((w) => (
            <Notice key={w} kind="warn">
              {w}
            </Notice>
          ))}
          <div className="grid three">
            {solution.objectives.map((o) => (
              <Stat
                key={o.name}
                label={`${o.name} (${o.sense})`}
                value={num(o.value)}
                note={o.unit ?? o.statement ?? undefined}
              />
            ))}
            <Stat
              label="Decisions"
              value={num(solution.decisions.length)}
              note="non-zero variables"
            />
            <Stat
              label="Binding constraints"
              value={num(solution.binding_constraints.length)}
              note="what limits the answer"
            />
            <Stat
              label="Solve time"
              value={`${num(solution.wall_time_seconds, 3)}s`}
              note={solution.gap !== null ? `gap ${num(solution.gap * 100, 3)}%` : "proved optimal"}
            />
          </div>

          {selection && (
            <p className="muted" style={{ marginTop: "0.75rem" }}>
              Chose <code>{selection.solver}</code> from{" "}
              {selection.considered.filter((c) => c.eligible).length} eligible engine(s)
              {selection.requested ? " (explicitly requested)" : " automatically"}.
            </p>
          )}
        </Card>
      )}
    </>
  );
}

function describeStage(message: Record<string, unknown>): string {
  if (message.stage === "compiled") {
    const stats = message.statistics as { variables: number; constraints: number } | undefined;
    return stats ? `${stats.variables} columns, ${stats.constraints} rows` : "";
  }
  if (message.stage === "solved") {
    return `${String(message.solver)} — ${String(message.status)} in ${num(
      Number(message.wall_time_seconds),
      3,
    )}s`;
  }
  if (message.stage === "error") return String(message.message ?? "");
  return "";
}

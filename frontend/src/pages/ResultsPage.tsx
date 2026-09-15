import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { Explanation, ProvenanceGraph, Solution } from "../lib/types";
import { Badge, Card, Empty, Notice, num, signed, statusKind } from "../components/common";

const CHAIN = [
  "source",
  "fact",
  "parameter",
  "constraint",
  "model",
  "run",
  "solution",
  "decision",
];

/**
 * Results, and the answer to "why?".
 *
 * The explanation is assembled from the compiled model and the solution vector
 * — binding constraints, shadow prices, the parameters feeding them and where
 * those came from. No language model is involved, which is what makes it
 * reproducible and defensible.
 */
export default function ResultsPage({
  solution,
  solutionId,
}: {
  solution: Solution | null;
  solutionId: string | null;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [graph, setGraph] = useState<ProvenanceGraph | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setExplanation(null);
    setGraph(null);
    setError(null);
    if (!solutionId) return;
    api
      .provenance(solutionId)
      .then(setGraph)
      .catch(() => setGraph(null));
  }, [solutionId]);

  async function explain(key: string) {
    if (!solutionId) return;
    setSelected(key);
    setError(null);
    try {
      setExplanation(await api.explain(solutionId, key));
    } catch (e) {
      setExplanation(null);
      setError(String((e as Error).message));
    }
  }

  if (!solution) return <Empty>Solve a problem to see results.</Empty>;

  if (!solution.decisions.length) {
    return (
      <Card title="No solution" actions={<Badge kind={statusKind(solution.status)}>{solution.status}</Badge>}>
        <Notice kind="bad">
          {solution.message ?? "The solver returned no usable solution."}
        </Notice>
        {solution.warnings.map((w) => (
          <Notice key={w} kind="warn">
            {w}
          </Notice>
        ))}
        <p className="muted">
          An infeasible result is information: it says the constraints and the data cannot both
          hold. Compare against the baseline scenario to see which change caused it.
        </p>
      </Card>
    );
  }

  return (
    <>
      <div className="grid two">
        <Card
          title="Recommended decisions"
          subtitle="Select one to see why it has the value it has."
        >
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Decision</th>
                  <th className="num">Value</th>
                  <th>Means</th>
                </tr>
              </thead>
              <tbody>
                {solution.decisions.map((d) => (
                  <tr
                    key={d.key}
                    className={`clickable ${selected === d.key ? "selected" : ""}`}
                    onClick={() => void explain(d.key)}
                  >
                    <td className="mono" title={d.key}>{d.label ?? d.key}</td>
                    <td className="num">{num(d.value)}</td>
                    <td className="muted">{d.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card
          title="What limits this answer"
          subtitle="Binding constraints, and what one more unit of headroom would be worth."
        >
          {solution.binding_constraints.length === 0 ? (
            <Empty>Nothing is binding — the objective alone determines the answer.</Empty>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Constraint</th>
                    <th className="num">At</th>
                    <th className="num">Worth</th>
                  </tr>
                </thead>
                <tbody>
                  {solution.binding_constraints.map((c) => (
                    <tr key={c.key}>
                      <td>
                        <div className="mono" title={c.key}>{c.label ?? c.key}</div>
                        <div className="muted">{c.statement}</div>
                      </td>
                      <td className="num">{num(c.rhs)}</td>
                      <td className="num">
                        {c.marginal_effect === null ? (
                          <span className="muted">—</span>
                        ) : (
                          <span title={`per unit change in ${c.marginal_objective}`}>
                            {signed(c.marginal_effect)}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!solution.duals_available && solution.duals_unavailable_reason && (
                <p className="muted" style={{ marginTop: "0.5rem" }}>
                  Marginal worth is blank because {solution.duals_unavailable_reason}.
                </p>
              )}
            </div>
          )}
        </Card>
      </div>

      {error && <Notice kind="bad">{error}</Notice>}

      {explanation && (
        <Card
          title={`Why ${explanation.label ?? explanation.decision}?`}
          subtitle="Derived from the model, not generated."
        >
          <div className="narrative">
            <ul>
              {explanation.narrative.map((line, index) => (
                <li key={index} className={line.startsWith("  ") || line.startsWith("    ") ? "indent" : ""}>
                  {line.trim()}
                </li>
              ))}
            </ul>
          </div>

          {explanation.limited_by.length > 0 && (
            <>
              <h3 style={{ marginTop: "1rem" }}>Evidence</h3>
              <table>
                <thead>
                  <tr>
                    <th>Constraint</th>
                    <th>Category</th>
                    <th>Fed by</th>
                  </tr>
                </thead>
                <tbody>
                  {explanation.limited_by.map((e) => (
                    <tr key={e.key}>
                      <td>
                        <div className="mono" title={e.key}>{e.label ?? e.key}</div>
                        <div className="muted">{e.statement}</div>
                      </td>
                      <td>
                        <Badge kind={e.category === "regulatory" ? "bad" : "neutral"}>
                          {e.category ?? "—"}
                        </Badge>
                      </td>
                      <td className="muted">
                        {e.parameters
                          .map(
                            (p) =>
                              `${p.parameter}${p.sources.length ? ` (${p.sources.join(", ")})` : ""}`,
                          )
                          .join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {explanation.ruled_out.length > 0 && (
            <>
              <h3 style={{ marginTop: "1rem" }}>What removed the alternatives</h3>
              <p className="muted fxs" style={{ marginTop: "-0.3rem", marginBottom: "0.5rem" }}>
                A rule that forbids something never mentions what was chosen, so these are the
                rows that closed off the other options.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Rule</th>
                    <th className="num">Options removed</th>
                    <th>Because of</th>
                  </tr>
                </thead>
                <tbody>
                  {explanation.ruled_out.slice(0, 8).map((e) => {
                    const named = [
                      ...new Set(
                        e.parameters.flatMap((p) =>
                          p.sources.filter((s) => s !== "user_input"),
                        ),
                      ),
                    ];
                    return (
                      <tr key={e.key}>
                        <td>
                          <div className="mono" title={e.key}>{e.label ?? e.key}</div>
                          <div className="muted">{e.statement}</div>
                        </td>
                        <td className="num">{num(e.alternatives_removed ?? 0, 0)}</td>
                        <td className="muted">
                          {named.length > 0
                            ? named.map((s) => s.replace("fixed_event:", "")).join(", ")
                            : "the data as entered"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}

          {explanation.assumptions.length > 0 && (
            <>
              <h3 style={{ marginTop: "1rem" }}>This rests on</h3>
              <ul className="muted">
                {explanation.assumptions.map((a) => (
                  <li key={a.key}>{a.statement}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      )}

      {graph && (
        <Card
          title="Provenance"
          subtitle="Recorded as the platform worked, not reconstructed afterwards."
        >
          <div className="chain" style={{ marginBottom: "0.9rem" }}>
            {CHAIN.map((kind, index) => (
              <span key={kind} className="row" style={{ gap: "0.35rem" }}>
                <span className="link">
                  {kind}
                  <span className="muted">
                    {" "}
                    ×{graph.nodes.filter((n) => n.kind === kind).length}
                  </span>
                </span>
                {index < CHAIN.length - 1 && <span className="arrow">→</span>}
              </span>
            ))}
          </div>
          <p className="muted">
            {graph.nodes.length} nodes and {graph.edges.length} edges connect every recommended
            decision back to the sources its numbers came from.
          </p>
          <details>
            <summary className="muted">Sources feeding this solution</summary>
            <table style={{ marginTop: "0.5rem" }}>
              <tbody>
                {graph.nodes
                  .filter((n) => n.kind === "source")
                  .map((n) => (
                    <tr key={n.id}>
                      <td className="mono">{n.label}</td>
                      <td className="muted">
                        {n.detail ?? "—"}
                        {" · "}
                        {graph.edges.filter((e) => e.source === n.id).length} assertion(s)
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </details>
        </Card>
      )}
    </>
  );
}

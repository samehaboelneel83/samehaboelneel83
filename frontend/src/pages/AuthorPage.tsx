import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import type { DslDiagnostic, TemplateSummary } from "../lib/types";
import { Badge, Card, Empty, Notice, Stat, num } from "../components/common";

/**
 * Write a problem as text.
 *
 * The point of this screen is that adding a *class* of problem no longer means
 * writing code. Every built-in template can be loaded as source, so the fastest
 * way to learn the language is to open a model you already trust and edit it.
 */
export default function AuthorPage({ onSaved }: { onSaved: (key: string) => void }) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [source, setSource] = useState<string>(STARTER);
  const [checked, setChecked] = useState<Awaited<ReturnType<typeof api.dslCheck>> | null>(null);
  const [problem, setProblem] = useState<DslDiagnostic | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api
      .templates()
      .then((body) => setTemplates(body.templates))
      .catch(() => undefined);
  }, []);

  function handle(e: unknown) {
    setChecked(null);
    if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
      const detail = e.detail as DslDiagnostic;
      if (detail.message) {
        setProblem(detail);
        setError(null);
        return;
      }
    }
    setProblem(null);
    setError(String((e as Error).message));
  }

  async function check() {
    setBusy("check");
    setProblem(null);
    setError(null);
    try {
      setChecked(await api.dslCheck(source));
    } catch (e) {
      handle(e);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    setProblem(null);
    setError(null);
    try {
      const saved = await api.dslSave(source);
      onSaved(saved.key);
    } catch (e) {
      handle(e);
    } finally {
      setBusy(null);
    }
  }

  async function load(key: string) {
    setBusy("load");
    setProblem(null);
    setError(null);
    try {
      const body = await api.dslTemplate(key);
      setSource(body.source);
      setChecked(null);
    } catch (e) {
      handle(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {error && <Notice kind="bad">{error}</Notice>}

      <Card
        title="Write a problem"
        subtitle="Sets, parameters, decisions, constraints, objectives, assumptions and scenarios — as text. No code."
        actions={
          <>
            <button className="secondary" onClick={check} disabled={busy !== null}>
              {busy === "check" ? "Checking…" : "Check"}
            </button>
            <button className="primary" onClick={save} disabled={busy !== null}>
              {busy === "save" ? "Saving…" : "Save problem"}
            </button>
          </>
        }
      >
        <div className="row" style={{ marginBottom: "0.7rem" }}>
          <span className="muted fxs">Start from a built-in model:</span>
          {templates.map((t) => (
            <button key={t.key} className="link" onClick={() => void load(t.key)}>
              {t.title}
            </button>
          ))}
        </div>

        <textarea
          id="dsl-source"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          spellCheck={false}
          style={{ minHeight: "420px" }}
        />

        {problem && (
          <div style={{ marginTop: "0.8rem" }}>
            <Notice kind="bad">
              <div>
                <strong>
                  {problem.line
                    ? `Line ${problem.line}, column ${problem.column}`
                    : problem.where
                      ? `In ${problem.where}`
                      : "Problem"}
                </strong>
                <div>{problem.message}</div>
              </div>
            </Notice>
            {problem.excerpt && <pre style={{ marginTop: "-0.4rem" }}>{problem.excerpt}</pre>}
            {problem.hint && (
              <p className="fxs muted" style={{ marginTop: "0.4rem" }}>
                {problem.hint}
              </p>
            )}
          </div>
        )}
      </Card>

      {checked && (
        <Card title={checked.name} subtitle={`Compiles cleanly as '${checked.key}'.`}>
          {checked.warnings.map((w) => (
            <Notice key={w} kind="warn">
              {w}
            </Notice>
          ))}
          <div className="grid three">
            <Stat label="Columns" value={num(checked.statistics.variables)} note="decision variables" />
            <Stat label="Rows" value={num(checked.statistics.constraints)} note="constraint instances" />
            <Stat label="Non-zeros" value={num(checked.statistics.nonzeros)} />
            <Stat
              label="Kind"
              value={checked.statistics.is_integer ? "Discrete" : "Continuous"}
              note={Object.entries(checked.statistics.variable_kinds)
                .map(([k, v]) => `${v} ${k}`)
                .join(", ")}
            />
            <Stat label="Structure" value={checked.structure ?? "general"} />
            <Stat
              label="Fingerprint"
              value={<span className="mono" style={{ fontSize: "0.9rem" }}>{checked.fingerprint.slice(0, 12)}</span>}
            />
          </div>

          <div className="grid two" style={{ marginTop: "1rem" }}>
            <div>
              <h3>Declares</h3>
              <table>
                <tbody>
                  {checked.summary.sets.map((s) => (
                    <tr key={s.name}>
                      <td className="mono">{s.name}</td>
                      <td>
                        <Badge kind={s.kind === "int" ? "accent" : "neutral"}>{s.kind}</Badge>
                      </td>
                      <td className="num">{s.elements}</td>
                    </tr>
                  ))}
                  {checked.summary.variables.map((v) => (
                    <tr key={v.name}>
                      <td className="mono">{v.name}</td>
                      <td>
                        <Badge kind="accent">{v.kind}</Badge>
                      </td>
                      <td className="muted">decision</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="fxs muted" style={{ marginTop: "0.5rem" }}>
                {checked.summary.parameters.length} parameter(s),{" "}
                {checked.summary.assumptions} assumption(s),{" "}
                {checked.summary.scenarios.length} scenario(s)
              </p>
            </div>
            <div>
              <h3>Rules</h3>
              <table>
                <tbody>
                  {checked.summary.constraints.map((c) => (
                    <tr key={c.name}>
                      <td className="mono">{c.name}</td>
                      <td className="muted">{c.statement}</td>
                    </tr>
                  ))}
                  {checked.summary.objectives.map((o) => (
                    <tr key={o.name}>
                      <td className="mono">{o.name}</td>
                      <td>
                        <Badge kind={o.sense === "maximize" ? "good" : "accent"}>{o.sense}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}

      {!checked && !problem && (
        <Card title="How it reads">
          <Empty>Check the source to see what it compiles to.</Empty>
        </Card>
      )}
    </>
  );
}

const STARTER = `problem crew_cover "Crew coverage"
  "Cover every shift with the fewest crews, respecting who is qualified."

set Crews = alpha, bravo, charlie
set Shifts = morning, evening, night

param cost[Crews] = { alpha: 3, bravo: 2, charlie: 4 }
param qualified[Crews, Shifts] default 1 = { (charlie, night): 0 }
param needed[Shifts] default 1

var assign[Crews, Shifts] binary means "Put this crew on this shift"

constraint cover "Every shift gets the crews it needs"
  category operational
  forall s in Shifts:
    sum(assign[c, s] for c in Crews) >= needed[s]

constraint one_shift "No crew works two shifts"
  category physical
  forall c in Crews:
    sum(assign[c, s] for s in Shifts) <= 1

constraint only_qualified "A crew is only put on shifts it is qualified for"
  category regulatory
  forall c in Crews, s in Shifts:
    assign[c, s] <= qualified[c, s]

minimize crew_cost "Use the cheapest crews that cover the week" unit "cost units":
  sum(cost[c] * assign[c, s] for c in Crews, s in Shifts)

assume crews_interchangeable "Any qualified crew covers a shift equally well"
  because "Skill differences beyond qualification are not modelled"
  affects crew_cost

scenario crew_lost "One crew unavailable"
  set qualified[alpha, morning] to 0
`;

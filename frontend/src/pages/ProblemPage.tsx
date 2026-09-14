import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import type { ProblemSpec, ProblemSummary, TemplateSummary } from "../lib/types";
import { Badge, Card, Empty, Notice } from "../components/common";

/**
 * State a problem: pick a template, supply the data, review what the template
 * actually committed you to. The assumptions panel is not decoration — a
 * template encodes choices the operator would otherwise never see.
 */
export default function ProblemPage({
  problems,
  selected,
  onSelect,
  onChanged,
}: {
  problems: ProblemSummary[];
  selected: string | null;
  onSelect: (key: string) => void;
  onChanged: () => void;
}) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [templateKey, setTemplateKey] = useState<string>("");
  const [data, setData] = useState<string>("");
  const [spec, setSpec] = useState<ProblemSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .templates()
      .then((body) => {
        setTemplates(body.templates);
        if (body.templates.length > 0) setTemplateKey(body.templates[0].key);
      })
      .catch((e) => setError(String(e.message)));
  }, []);

  useEffect(() => {
    if (!templateKey) return;
    api
      .templateExample(templateKey)
      .then((body) => setData(JSON.stringify(body.data, null, 2)))
      .catch((e) => setError(String(e.message)));
  }, [templateKey]);

  useEffect(() => {
    if (!selected) {
      setSpec(null);
      return;
    }
    api
      .problem(selected)
      .then((body) => setSpec(body.spec))
      .catch((e) => setError(String(e.message)));
  }, [selected]);

  const template = useMemo(
    () => templates.find((t) => t.key === templateKey) ?? null,
    [templates, templateKey],
  );

  async function create() {
    setError(null);
    setBusy(true);
    try {
      const parsed = JSON.parse(data) as Record<string, unknown>;
      const created = await api.instantiate(templateKey, parsed);
      setSpec(created.spec);
      onSelect(created.key);
      onChanged();
    } catch (e) {
      setError(
        e instanceof SyntaxError ? `The data is not valid JSON: ${e.message}` : String((e as Error).message),
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove(key: string) {
    await api.deleteProblem(key);
    if (selected === key) onSelect("");
    onChanged();
  }

  return (
    <>
      {error && <Notice kind="bad">{error}</Notice>}

      <div className="grid two">
        <Card
          title="State a problem"
          subtitle="A template turns a table of data into a defensible problem model."
        >
          <label className="field" style={{ marginBottom: "0.6rem" }}>
            Template
            <select value={templateKey} onChange={(e) => setTemplateKey(e.target.value)}>
              {templates.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.title}
                </option>
              ))}
            </select>
          </label>

          {template && (
            <>
              <p className="muted">{template.summary}</p>
              <div className="row" style={{ marginBottom: "0.6rem" }}>
                {template.tags.map((tag) => (
                  <Badge key={tag}>{tag}</Badge>
                ))}
              </div>
              <details style={{ marginBottom: "0.6rem" }}>
                <summary className="muted">Inputs this template expects</summary>
                <table style={{ marginTop: "0.5rem" }}>
                  <tbody>
                    {template.inputs.map((input) => (
                      <tr key={input.key}>
                        <td className="mono">{input.key}</td>
                        <td>
                          {input.description}
                          {!input.required && <span className="muted"> (optional)</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </>
          )}

          <label className="field">
            Data
            <textarea value={data} onChange={(e) => setData(e.target.value)} spellCheck={false} />
          </label>
          <div className="row end" style={{ marginTop: "0.6rem" }}>
            <button className="primary" onClick={create} disabled={busy || !templateKey}>
              {busy ? "Building…" : "Create problem"}
            </button>
          </div>
        </Card>

        <Card title="Problems" subtitle="Stated problems, ready to compile and solve.">
          {problems.length === 0 ? (
            <Empty>Nothing stated yet.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Name</th>
                  <th className="num">Vars</th>
                  <th className="num">Cons</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {problems.map((p) => (
                  <tr
                    key={p.key}
                    className={`clickable ${selected === p.key ? "selected" : ""}`}
                    onClick={() => onSelect(p.key)}
                  >
                    <td className="mono">{p.key}</td>
                    <td>{p.name}</td>
                    <td className="num">{p.variables}</td>
                    <td className="num">{p.constraints}</td>
                    <td>
                      <button
                        className="link"
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(p.key);
                        }}
                      >
                        delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {spec && <ProblemDetail spec={spec} />}
    </>
  );
}

function ProblemDetail({ spec }: { spec: ProblemSpec }) {
  return (
    <>
      <Card title={spec.name} subtitle={spec.description ?? undefined}>
        <div className="grid two">
          <div>
            <h3>Decisions</h3>
            <table>
              <tbody>
                {spec.variables.map((v) => (
                  <tr key={v.name}>
                    <td className="mono">
                      {v.name}
                      {v.index_sets.length > 0 && `[${v.index_sets.join(", ")}]`}
                    </td>
                    <td>
                      <Badge kind="accent">{v.kind}</Badge>
                    </td>
                    <td>{v.decision_meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3 style={{ marginTop: "1rem" }}>Objectives</h3>
            {spec.objectives.map((o) => (
              <p key={o.name}>
                <Badge kind={o.sense === "maximize" ? "good" : "accent"}>{o.sense}</Badge>{" "}
                {o.statement}
              </p>
            ))}

            <h3 style={{ marginTop: "1rem" }}>Index sets</h3>
            <table>
              <tbody>
                {spec.sets.map((s) => (
                  <tr key={s.name}>
                    <td className="mono">{s.name}</td>
                    <td className="num">{s.elements.length}</td>
                    <td className="muted">
                      {s.elements.slice(0, 6).join(", ")}
                      {s.elements.length > 6 && ` … +${s.elements.length - 6}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h3>Constraints</h3>
            <table>
              <tbody>
                {spec.constraints.map((c) => (
                  <tr key={c.name}>
                    <td>
                      <Badge kind={c.category === "regulatory" ? "bad" : "neutral"}>
                        {c.category}
                      </Badge>
                    </td>
                    <td>
                      {c.statement}
                      {c.rationale && <div className="muted">{c.rationale}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Card>

      <div className="grid two">
        <Card
          title="Assumptions"
          subtitle="What this model takes as true but cannot prove. Read these before trusting an answer."
        >
          {spec.assumptions.length === 0 ? (
            <Empty>No assumptions recorded.</Empty>
          ) : (
            <table>
              <tbody>
                {spec.assumptions.map((a) => (
                  <tr key={a.key}>
                    <td className="mono">{a.key}</td>
                    <td>
                      {a.statement}
                      {a.rationale && <div className="muted">{a.rationale}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Parameters" subtitle="Known quantities, with the source of each value.">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Indexed by</th>
                  <th className="num">Values</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {spec.parameters.map((p) => {
                  const sources = new Set(
                    p.values.map((v) => v.origin?.source ?? "unattributed"),
                  );
                  return (
                    <tr key={p.name}>
                      <td className="mono">{p.name}</td>
                      <td className="muted">{p.index_sets.join(", ") || "scalar"}</td>
                      <td className="num">{p.values.length}</td>
                      <td className="muted">{[...sources].join(", ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}

export { ApiError };

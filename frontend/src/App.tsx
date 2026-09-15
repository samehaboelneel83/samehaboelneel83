import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./lib/api";
import type { Diagnosis, ProblemSummary, Solution } from "./lib/types";
import { Badge, Notice } from "./components/common";
import AuthorPage from "./pages/AuthorPage";
import DomainPage from "./pages/DomainPage";
import ModelPage from "./pages/ModelPage";
import ProblemPage from "./pages/ProblemPage";
import ResultsPage from "./pages/ResultsPage";
import ScenarioPage from "./pages/ScenarioPage";
import SolvePage from "./pages/SolvePage";

/**
 * The navigation is the platform's pipeline, in order:
 *
 *   Domain → Problem → Model → Scenario → Solve → Results
 *
 * Steps that need a problem stay disabled until one exists, so the interface
 * cannot lead anyone into a screen that has nothing to show.
 */
const STEPS = [
  { key: "domain", label: "Domain", needsProblem: false },
  { key: "author", label: "Author", needsProblem: false },
  { key: "problem", label: "Problem", needsProblem: false },
  { key: "model", label: "Model", needsProblem: true },
  { key: "scenario", label: "Scenario", needsProblem: true },
  { key: "solve", label: "Solve", needsProblem: true },
  { key: "results", label: "Results", needsProblem: true },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

export default function App() {
  const [step, setStep] = useState<StepKey>("problem");
  const [problems, setProblems] = useState<ProblemSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [solution, setSolution] = useState<Solution | null>(null);
  const [solutionId, setSolutionId] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [health, setHealth] = useState<{ authentication: string; warnings: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.problems();
      setProblems(list);
      setSelected((current) => current ?? (list.length > 0 ? list[0].key : null));
    } catch (e) {
      setError(
        `Cannot reach the API: ${(e as Error).message}. Is the backend running on port 8000?`,
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
    api
      .health()
      .then(setHealth)
      .catch(() => undefined);
  }, [refresh]);

  const current = useMemo(
    () => problems.find((p) => p.key === selected) ?? null,
    [problems, selected],
  );
  const scenarios = current?.scenarios ?? [];

  function choose(key: string) {
    setSelected(key || null);
    // A solution belongs to the problem it came from; carrying it across would
    // show results for something the user is no longer looking at.
    setSolution(null);
    setSolutionId(null);
    setDiagnosis(null);
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Problem-Solving Platform</span>
        <nav className="pipeline">
          {STEPS.map((s, index) => (
            <button
              key={s.key}
              aria-current={step === s.key}
              disabled={s.needsProblem && !selected}
              onClick={() => setStep(s.key)}
            >
              <span className="step-index">{index + 1}</span>
              {s.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        {current && (
          <span className="row">
            <span className="muted">problem</span>
            <Badge kind="accent">{current.key}</Badge>
          </span>
        )}
        {health && health.authentication === "disabled" && (
          <Badge kind="warn">auth disabled</Badge>
        )}
      </header>

      <main>
        {error && <Notice kind="bad">{error}</Notice>}
        {health?.warnings.map((warning) => (
          <Notice key={warning} kind="warn">
            {warning}
          </Notice>
        ))}

        {step === "domain" && <DomainPage />}
        {step === "author" && (
          <AuthorPage
            onSaved={(key) => {
              void refresh();
              choose(key);
              setStep("problem");
            }}
          />
        )}
        {step === "problem" && (
          <ProblemPage
            problems={problems}
            selected={selected}
            onSelect={choose}
            onChanged={() => void refresh()}
          />
        )}
        {step === "model" && <ModelPage problemKey={selected} scenarios={scenarios} />}
        {step === "scenario" && <ScenarioPage problemKey={selected} scenarios={scenarios} />}
        {step === "solve" && (
          <SolvePage
            problemKey={selected}
            scenarios={scenarios}
            onSolved={({ solutionId: id, solution: result, diagnosis: why }) => {
              setSolution(result);
              setSolutionId(id);
              setDiagnosis(why);
              setStep("results");
            }}
          />
        )}
        {step === "results" && (
          <ResultsPage solution={solution} solutionId={solutionId} diagnosis={diagnosis} />
        )}
      </main>
    </div>
  );
}

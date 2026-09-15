/**
 * API client.
 *
 * Errors from the platform are structured — a compile failure names the
 * constraint it failed in, a rejected solver names the engines it considered —
 * so the client preserves that detail instead of flattening every failure into
 * a status code.
 */
import type {
  CompileResponse,
  ModelStatistics,
  ComparisonRow,
  Explanation,
  ProblemSpec,
  ProblemSummary,
  ProvenanceGraph,
  SolveResponse,
  SolverDescription,
  TemplateSummary,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(ApiError.describe(detail, status));
    this.status = status;
    this.detail = detail;
  }

  private static describe(detail: unknown, status: number): string {
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      if (typeof record.error === "string") {
        return record.where ? `${record.error} (in ${record.where})` : record.error;
      }
    }
    return `Request failed with status ${status}`;
  }

  /** Solver eligibility, when the failure was "no engine can take this". */
  get considered(): Array<{ solver: string; eligible: boolean; reason: string | null }> {
    const detail = this.detail as Record<string, unknown> | null;
    const considered = detail?.considered;
    return Array.isArray(considered) ? considered : [];
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail: unknown = await response.text();
    try {
      detail = JSON.parse(detail as string).detail;
    } catch {
      /* a non-JSON body is used as-is */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; authentication: string; warnings: string[] }>("/health"),
  solvers: () => request<{ solvers: SolverDescription[]; execution: string }>("/solvers"),

  templates: () => request<{ templates: TemplateSummary[] }>("/templates"),
  templateExample: (key: string) =>
    request<{ template: string; data: Record<string, unknown> }>(`/templates/${key}/example`),

  problems: () => request<ProblemSummary[]>("/problems"),
  problem: (key: string) => request<{ spec: ProblemSpec }>(`/problems/${key}`),
  instantiate: (template: string, data: Record<string, unknown>) =>
    request<{ key: string; spec: ProblemSpec }>("/problems", {
      method: "POST",
      body: JSON.stringify({ template, data, save: true }),
    }),
  deleteProblem: (key: string) => request<void>(`/problems/${key}`, { method: "DELETE" }),

  compile: (key: string, scenario?: string | null) =>
    request<CompileResponse>(
      `/problems/${key}/compile${scenario ? `?scenario=${encodeURIComponent(scenario)}` : ""}`,
      { method: "POST" },
    ),
  solve: (key: string, body: Record<string, unknown>) =>
    request<SolveResponse>(`/problems/${key}/solve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  compare: (key: string, scenarios: Array<string | null>) =>
    request<{ comparison: ComparisonRow[] }>(`/problems/${key}/compare`, {
      method: "POST",
      body: JSON.stringify({ scenarios }),
    }),
  sensitivity: (key: string, body: Record<string, unknown>) =>
    request<{
      method: string;
      objective_name: string | null;
      baseline_objective: number | null;
      ranking: Array<Record<string, unknown>>;
      points: Array<Record<string, unknown>>;
      notes: string[];
    }>(`/problems/${key}/sensitivity`, { method: "POST", body: JSON.stringify(body) }),

  explain: (solutionId: string, decision: string) =>
    request<Explanation>(
      `/solutions/${solutionId}/explain?decision=${encodeURIComponent(decision)}`,
    ),
  provenance: (solutionId: string) =>
    request<ProvenanceGraph>(`/solutions/${solutionId}/provenance`),

  runs: (problem?: string) =>
    request<{ runs: Array<Record<string, unknown>> }>(
      `/runs${problem ? `?problem=${encodeURIComponent(problem)}` : ""}`,
    ),

  dslCheck: (source: string) =>
    request<{
      key: string;
      name: string;
      statistics: ModelStatistics;
      fingerprint: string;
      structure: string | null;
      warnings: string[];
      summary: {
        sets: Array<{ name: string; elements: number; kind: string }>;
        parameters: string[];
        variables: Array<{ name: string; kind: string }>;
        constraints: Array<{ name: string; statement: string }>;
        objectives: Array<{ name: string; sense: string }>;
        assumptions: number;
        scenarios: string[];
      };
    }>("/dsl/check", { method: "POST", body: JSON.stringify({ source }) }),
  dslSave: (source: string) =>
    request<{ key: string; id: string }>("/dsl/problems", {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  dslExport: (key: string) => request<{ source: string }>(`/dsl/problems/${key}`),
  dslTemplate: (key: string) =>
    request<{ template: string; title: string; source: string }>(`/dsl/templates/${key}`),

  entityTypes: () =>
    request<{ entity_types: Array<{ key: string; name: string; entities: number }> }>(
      "/domain/entity-types",
    ),
  entities: (entityType?: string) =>
    request<{
      entities: Array<{
        key: string;
        name: string;
        entity_type: string;
        attributes: Record<string, unknown>;
      }>;
    }>(`/domain/entities${entityType ? `?entity_type=${encodeURIComponent(entityType)}` : ""}`),
  createEntityType: (key: string, name: string) =>
    request<{ id: string }>("/domain/entity-types", {
      method: "POST",
      body: JSON.stringify({ key, name }),
    }),
  createEntity: (body: Record<string, unknown>) =>
    request<{ id: string }>("/domain/entities", { method: "POST", body: JSON.stringify(body) }),

  simulateSweep: (body: Record<string, unknown>) =>
    request<{
      sweep: Array<{
        servers: number;
        utilisation: number;
        mean_wait_minutes: number;
        p95_wait_minutes: number;
        stable: boolean;
      }>;
    }>("/simulate/queue/sweep", { method: "POST", body: JSON.stringify(body) }),
};

export interface SolveStage {
  stage: string;
  [key: string]: unknown;
}

/**
 * Solve over a WebSocket, reporting each stage as it starts.
 *
 * Compiling a large time-indexed model can take as long as solving a small one,
 * so naming the current stage is more honest than one indeterminate spinner.
 */
export function solveStreaming(
  problem: string,
  options: { scenario?: string | null; solver?: string | null; timeLimit?: number },
  onStage: (stage: SolveStage) => void,
): Promise<SolveStage> {
  return new Promise((resolve, reject) => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws/solve`);

    socket.onopen = () =>
      socket.send(
        JSON.stringify({
          problem,
          scenario: options.scenario ?? null,
          solver: options.solver ?? null,
          time_limit_seconds: options.timeLimit ?? null,
        }),
      );
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as SolveStage;
      onStage(message);
      if (message.stage === "complete") {
        socket.close();
        resolve(message);
      } else if (message.stage === "error") {
        socket.close();
        reject(new Error(String(message.message ?? "the solve failed")));
      }
    };
    socket.onerror = () => reject(new Error("the connection to the solver was lost"));
  });
}

export interface TemplateInput {
  key: string;
  label: string;
  kind: string;
  description: string;
  required: boolean;
  columns: string[];
  default: unknown;
}

export interface TemplateSummary {
  key: string;
  title: string;
  summary: string;
  category: string;
  tags: string[];
  inputs: TemplateInput[];
}

export interface ProblemSummary {
  id: string;
  key: string;
  name: string;
  description: string | null;
  template_key: string | null;
  status: string;
  variables: number;
  constraints: number;
  scenarios: string[];
  updated_at: string | null;
}

export interface ProblemSpec {
  key: string;
  name: string;
  problem_type: string;
  description: string | null;
  sets: Array<{ name: string; kind: string; elements: string[]; description: string | null }>;
  parameters: Array<{
    name: string;
    index_sets: string[];
    unit: string | null;
    description: string | null;
    values: Array<{ index: string[]; value: number; origin: { source: string } | null }>;
  }>;
  variables: Array<{
    name: string;
    index_sets: string[];
    kind: string;
    lb: number;
    ub: number | null;
    decision_meaning: string | null;
  }>;
  constraints: Array<{
    name: string;
    statement: string;
    category: string;
    rationale: string | null;
  }>;
  objectives: Array<{ name: string; statement: string; sense: string; unit: string | null }>;
  assumptions: Array<{ key: string; statement: string; rationale: string | null; affects: string[] }>;
  scenarios: Array<{ key: string; name: string; description: string | null; overrides: unknown[] }>;
}

export interface ModelStatistics {
  variables: number;
  constraints: number;
  nonzeros: number;
  variable_kinds: Record<string, number>;
  is_integer: boolean;
}

export interface CompileResponse {
  fingerprint: string;
  scenario: string | null;
  statistics: ModelStatistics;
  structure: string | null;
  record: {
    mappings: Array<{
      ir_kind: string;
      ir_name: string;
      problem_kind: string;
      problem_name: string;
      statement: string | null;
    }>;
    applied_overrides: Array<{
      parameter: string;
      index: string[];
      baseline: number;
      value: number;
    }>;
    warnings: string[];
  };
  constraint_rows: Array<{
    key: string;
    name: string;
    statement: string | null;
    op: string;
    rhs: number;
    terms: number;
  }>;
}

export interface ObjectiveValue {
  name: string;
  sense: string;
  value: number;
  unit: string | null;
  statement: string | null;
}

export interface Decision {
  variable: string;
  index: string[];
  key: string;
  value: number;
  meaning: string | null;
}

export interface ConstraintOutcome {
  key: string;
  name: string;
  statement: string | null;
  index: string[];
  activity: number;
  op: string;
  rhs: number;
  slack: number;
  binding: boolean;
  marginal_effect: number | null;
  marginal_objective: string | null;
}

export interface Solution {
  status: string;
  solver: string;
  objectives: ObjectiveValue[];
  decisions: Decision[];
  binding_constraints: ConstraintOutcome[];
  slack_constraints: ConstraintOutcome[];
  duals_available: boolean;
  duals_unavailable_reason: string | null;
  wall_time_seconds: number;
  gap: number | null;
  message: string | null;
  warnings: string[];
}

export interface SolveResponse {
  run_id: string;
  solution_id: string | null;
  model_version_id: string;
  fingerprint: string;
  statistics: ModelStatistics;
  selection: {
    solver: string;
    requested: string | null;
    considered: Array<{ solver: string; eligible: boolean; reason: string | null }>;
  };
  solution: Solution;
}

export interface Explanation {
  decision: string;
  value: number;
  meaning: string | null;
  objective_contribution: Record<string, number>;
  limited_by: Array<{
    key: string;
    statement: string | null;
    category: string | null;
    rationale: string | null;
    coefficient: number;
    marginal_effect: number | null;
    marginal_objective: string | null;
    parameters: Array<{ parameter: string; description: string | null; sources: string[] }>;
  }>;
  assumptions: Array<{ key: string; statement: string; rationale: string | null }>;
  scenario: string | null;
  narrative: string[];
}

export interface ProvenanceGraph {
  nodes: Array<{ id: string; kind: string; label: string; detail: string | null }>;
  edges: Array<{ source: string; target: string; relation: string }>;
}

export interface ComparisonRow {
  scenario: string;
  status: string;
  solver?: string;
  objectives: ObjectiveValue[];
  run_id?: string;
  solution_id?: string;
  decisions?: number;
  binding_constraints?: number;
  delta_vs_baseline?: number;
  warnings?: string[];
  error?: string;
}

export interface SolverDescription {
  name: string;
  module: string;
  capabilities: Record<string, boolean | string | null>;
}

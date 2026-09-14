import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

export function Notice({ kind, children }: { kind: "info" | "warn" | "bad"; children: ReactNode }) {
  return <div className={`notice ${kind}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Badge({
  kind = "neutral",
  children,
}: {
  kind?: "good" | "warn" | "bad" | "neutral" | "accent";
  children: ReactNode;
}) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function statusKind(status: string): "good" | "warn" | "bad" | "neutral" {
  if (status === "optimal") return "good";
  if (status === "feasible" || status === "timeout") return "warn";
  if (status === "infeasible" || status === "unbounded" || status === "error") return "bad";
  return "neutral";
}

/** Numbers in this interface are read, compared and checked, so they are
 *  formatted for reading rather than printed at full float precision. */
export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  if (Math.abs(value) >= 1e6 || (Math.abs(value) < 1e-4 && value !== 0)) {
    return value.toExponential(2);
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${num(value, digits)}`;
}

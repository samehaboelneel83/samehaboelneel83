"""Problem Model -> source text.

Two jobs. It closes the loop, so a problem can be read out, edited and read
back; and it turns the built-in templates into worked examples, since the
fastest way to learn the language is to see a model you already trust written
in it.

The output must parse back to the same model — there is a test that takes every
template through text and compares IR fingerprints.
"""

from __future__ import annotations

from psp.dsl.lexer import KEYWORDS
from psp.ir.expr import Rel
from psp.problem.spec import ProblemSpec

RELATION = {"le": "<=", "ge": ">=", "eq": "=="}
COMPARISON = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
# Binding strength, so parentheses appear only where they change the meaning.
PRECEDENCE = {"add": 1, "sub": 1, "mul": 2, "div": 2, "neg": 3}


def write_problem(spec: ProblemSpec) -> str:
    """Render a problem as source text."""
    out: list[str] = [f"problem {_name(spec.key)} {_quote(spec.name)}"]
    if spec.description:
        out.append(f"  {_quote(spec.description)}")
    out.append("")

    for s in spec.sets:
        out.append(_set(s))
    if spec.sets:
        out.append("")

    for h in spec.hierarchies:
        out.extend(_hierarchy(h))
        out.append("")

    # A derived table is written as the tree it came from, not as itself — the
    # whole point of the construct is that nobody maintains the consequences.
    written = [p for p in spec.parameters if p.derived_from is None]
    for p in written:
        out.append(_param(p))
    if written:
        out.append("")

    for v in spec.variables:
        out.append(_var(v))
    if spec.variables:
        out.append("")

    for c in spec.constraints:
        out.extend(_constraint(c))
        out.append("")

    for o in spec.objectives:
        out.extend(_objective(o))
        out.append("")

    for a in spec.assumptions:
        out.extend(_assumption(a))
    if spec.assumptions:
        out.append("")

    for sc in spec.scenarios:
        out.extend(_scenario(sc))
        out.append("")

    if spec.structure is not None:
        out.append(f"structure {spec.structure.kind}")
        for field, value in spec.structure.model_dump(exclude_none=True).items():
            if field != "kind":
                out.append(f"  {field} {value}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------- primitives


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _name(text: str) -> str:
    """Names are bare when they can be, quoted when they would be misread.

    A name that happens to be a keyword is left bare. The parser takes a
    keyword wherever it takes a name, and quoting one produced source it could
    not read back: a model with a parameter called ``weight`` was written as
    ``"weight"[c]``, which reads as a string followed by a stray bracket.
    """
    if not text:
        return '""'
    usable = text.replace("_", "a").replace(".", "a")
    if usable.isalnum() and not text[0].isdigit():
        return text
    return _quote(text)


def _element(text: str) -> str:
    if text.lstrip("-").isdigit():
        return text
    return _name(text)


def _number(value: float) -> str:
    if value == float("inf"):
        return "inf"
    if value == float("-inf"):
        return "-inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


# ----------------------------------------------------------- declarations


def _set(s) -> str:
    if s.from_entity_type and not s.elements:
        return f"set {_name(s.name)} from {_name(s.from_entity_type)}"
    kind = " : int" if s.kind == "int" else ""
    if s.kind == "int" and _is_contiguous(s.elements):
        body = f"{s.elements[0]}..{s.elements[-1]}"
    else:
        body = ", ".join(_element(e) for e in s.elements)
    line = f"set {_name(s.name)}{kind} = {body}"
    if s.labels:
        pairs = ", ".join(f"{_element(k)}: {_quote(v)}" for k, v in s.labels.items())
        line += " labels { " + pairs + " }"
    if s.entity_type:
        line += f" of {_name(s.entity_type)}"
    if s.description:
        line += f" {_quote(s.description)}"
    return line


def _is_contiguous(elements: list[str]) -> bool:
    try:
        numbers = [int(e) for e in elements]
    except ValueError:
        return False
    return bool(numbers) and numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def _param(p) -> str:
    head = f"param {_name(p.name)}"
    if p.index_sets:
        head += "[" + ", ".join(_name(s) for s in p.index_sets) + "]"
    if p.default is not None:
        head += f" default {_number(p.default)}"
    if p.unit:
        head += f" unit {_quote(p.unit)}"
    if p.description:
        head += f" {_quote(p.description)}"
    if p.from_attribute and not p.values:
        return f"{head} from attribute {_name(p.from_attribute)}"
    if not p.values:
        return head
    if not p.index_sets:
        return f"{head} = {_number(p.values[0].value)}"

    entries = []
    for value in p.values:
        key = ("(" + ", ".join(_element(i) for i in value.index) + ")"
               if len(value.index) > 1 else _element(value.index[0]))
        entries.append(f"{key}: {_number(value.value)}")
    # Long tables go one line per entry; the lexer continues inside braces.
    if len(entries) > 6:
        return head + " = {\n    " + ",\n    ".join(entries) + " }"
    return head + " = { " + ", ".join(entries) + " }"


def _var(v) -> str:
    head = f"var {_name(v.name)}"
    if v.index_sets:
        head += "[" + ", ".join(_name(s) for s in v.index_sets) + "]"
    head += f" {v.kind}"
    # Binary already means [0, 1]; saying so again is noise.
    if not (v.kind == "binary" and v.lb == 0.0 and v.ub == 1.0):
        head += f" in [{_number(v.lb)}, {_number(v.ub if v.ub is not None else float('inf'))}]"
    if v.description:
        head += f" {_quote(v.description)}"
    if v.decision_meaning:
        head += f" means {_quote(v.decision_meaning)}"
    return head


def _constraint(c) -> list[str]:
    lines = [f"constraint {_name(c.name)} {_quote(c.statement)}"]
    if c.category != "operational":
        lines.append(f"  category {c.category}")
    if c.penalty is not None:
        lines.append(f"  soft penalty {_expr(c.penalty)}")
    if c.rationale:
        lines.append(f"  because {_quote(c.rationale)}")
    condition = ""
    if c.when is not None:
        negated = "not " if c.when_is == 0 else ""
        condition = f"if {negated}{_expr(c.when)} then "
    header = ""
    if c.forall:
        header = "forall " + ", ".join(f"{b.index} in {_name(b.set)}" for b in c.forall)
    if c.where is not None:
        header = (header + " " if header else "") + f"where {_predicate(c.where)}"
    if header:
        lines.append(f"  {header}:")
    lines.append(f"    {condition}{_relation(c.rel)}")
    return lines


def _relation(rel: Rel) -> str:
    return f"{_expr(rel.lhs)} {RELATION[rel.op]} {_expr(rel.rhs)}"


def _objective(o) -> list[str]:
    head = f"{o.sense} {_name(o.name)} {_quote(o.statement)}"
    if o.unit:
        head += f" unit {_quote(o.unit)}"
    if o.weight != 1.0:
        head += f" weight {_number(o.weight)}"
    return [head + ":", f"  {_expr(o.expr)}"]


def _assumption(a) -> list[str]:
    lines = [f"assume {_name(a.key)} {_quote(a.statement)}"]
    if a.rationale:
        lines.append(f"  because {_quote(a.rationale)}")
    if a.affects:
        lines.append("  affects " + ", ".join(_name(x) for x in a.affects))
    return lines


def _scenario(sc) -> list[str]:
    lines = [f"scenario {_name(sc.key)} {_quote(sc.name)}"]
    if sc.description:
        lines.append(f"  {_quote(sc.description)}")
    for o in sc.overrides:
        target = _name(o.parameter)
        if o.index:
            target += "[" + ", ".join(_element(i) for i in o.index) + "]"
        if o.scale is not None:
            lines.append(f"  scale {target} by {_number(o.scale)}")
        else:
            lines.append(f"  set {target} to {_number(o.value)}")
    return lines


# ------------------------------------------------------------ expressions


def _expr(node, parent: int = 0) -> str:
    op = node.op
    if op == "const":
        return _number(node.value)
    if op == "lit":
        return _quote(node.value)
    if op == "idx":
        return node.name
    if op in ("param", "var"):
        if not node.index:
            return _name(node.name)
        return _name(node.name) + "[" + ", ".join(_expr(a) for a in node.index) + "]"
    if op == "neg":
        return f"-{_expr(node.arg, PRECEDENCE['neg'])}"
    if op in ("add", "sub", "mul", "div"):
        symbol = {"add": " + ", "sub": " - ", "mul": " * ", "div": " / "}[op]
        level = PRECEDENCE[op]
        text = symbol.join(_expr(a, level) for a in node.args)
        return f"({text})" if level < parent else text
    if op == "sum":
        over = ", ".join(f"{b.index} in {_name(b.set)}" for b in node.over)
        text = f"sum({_expr(node.body)} for {over}"
        if node.where is not None:
            text += f" where {_predicate(node.where)}"
        return text + ")"
    raise ValueError(f"cannot write expression node '{op}'")


def _predicate(node, nested: bool = False) -> str:
    pred = node.pred
    if pred == "cmp":
        return f"{_expr(node.lhs)} {COMPARISON[node.op]} {_expr(node.rhs)}"
    if pred in ("and", "or"):
        joined = f" {pred} ".join(_predicate(a, True) for a in node.args)
        return f"({joined})" if nested else joined
    if pred == "not":
        return f"not {_predicate(node.arg, True)}"
    raise ValueError(f"cannot write predicate node '{pred}'")


def _hierarchy(h) -> list[str]:
    head = f"hierarchy {_name(h.name)} by parent"
    if h.from_relationship and not h.parent:
        lines = [head]
        for clause, name in (("covers", h.covers), ("overlap", h.overlap),
                             ("leaf", h.leaf), ("count", h.count), ("depth", h.depth)):
            if name:
                lines.append(f"  {clause} {_name(name)}")
        lines.append(f"  from {_name(h.from_relationship)}")
        return lines
    lines = [head]
    for clause, name in (("covers", h.covers), ("overlap", h.overlap),
                         ("leaf", h.leaf), ("count", h.count), ("depth", h.depth)):
        if name:
            lines.append(f"  {clause} {_name(name)}")
    pairs = [f"{_name(child)}: {_name(parent)}" for child, parent in h.parent.items()]
    lines.append("  = { " + ", ".join(pairs) + " }")
    return lines

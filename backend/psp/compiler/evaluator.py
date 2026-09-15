"""Affine evaluation of IR expressions.

The compiler walks each expression once and folds it into an :class:`Affine`
form — a constant plus a sparse map of variable keys to coefficients. Constants
collapse eagerly, so by the time a constraint reaches a solver it is already a
sparse row.

Objectives are walked in a second algebra, :class:`Quadratic`, which carries
degree-two terms as well. The walk is the same; only the multiplication rule
differs, so there is one expression language and not two. Everywhere else a
product whose factors both carry variables is rejected here rather than
silently mis-solved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from psp.compiler.errors import DomainError, NonLinearError, UnboundIndexError
from psp.ir.model import IRModel

# Element labels are compared and hashed as strings; numeric-set elements are
# normalised through int() so "07" and "7" can never denote different elements.
Value = float | str


@dataclass
class Affine:
    """``constant + sum(coef * var)``."""

    constant: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)

    @property
    def is_constant(self) -> bool:
        return not self.terms

    def __add__(self, other: Affine) -> Affine:
        out = Affine(self.constant + other.constant, dict(self.terms))
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v
        return out.prune()

    def __neg__(self) -> Affine:
        return Affine(-self.constant, {k: -v for k, v in self.terms.items()})

    def __sub__(self, other: Affine) -> Affine:
        return self + (-other)

    def scale(self, factor: float) -> Affine:
        scaled = {k: v * factor for k, v in self.terms.items()}
        return Affine(self.constant * factor, scaled).prune()

    def prune(self, eps: float = 1e-12) -> Affine:
        self.terms = {k: v for k, v in self.terms.items() if abs(v) > eps}
        return self


@dataclass
class Quadratic:
    """``constant + sum(coef * var) + sum(coef * var * var)``.

    The algebra objectives are folded in. It is deliberately not the algebra
    constraints use: a quadratic *row* is a different kind of problem from a
    quadratic *objective*, and no engine behind this platform accepts one.
    """

    constant: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)
    # Keyed by an ordered pair of variable keys, so ``x*y`` and ``y*x`` land on
    # one coefficient rather than two entries.
    #
    # This is canonicalisation, not correctness: both engines symmetrise, so the
    # unordered form gives the same answer. What it buys is one representation
    # for one objective — which matters because the flat signature is what
    # regression tests pin, and because it halves the terms a solver is handed.
    quad: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def is_constant(self) -> bool:
        return not self.terms and not self.quad

    @property
    def is_affine(self) -> bool:
        return not self.quad

    def __add__(self, other: Quadratic) -> Quadratic:
        out = Quadratic(self.constant + other.constant, dict(self.terms), dict(self.quad))
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v
        for k, v in other.quad.items():
            out.quad[k] = out.quad.get(k, 0.0) + v
        return out.prune()

    def __neg__(self) -> Quadratic:
        return Quadratic(
            -self.constant,
            {k: -v for k, v in self.terms.items()},
            {k: -v for k, v in self.quad.items()},
        )

    def __sub__(self, other: Quadratic) -> Quadratic:
        return self + (-other)

    def scale(self, factor: float) -> Quadratic:
        return Quadratic(
            self.constant * factor,
            {k: v * factor for k, v in self.terms.items()},
            {k: v * factor for k, v in self.quad.items()},
        ).prune()

    def multiply(self, other: Quadratic) -> Quadratic:
        """``self * other``, valid only while the result stays degree two."""
        if self.quad or other.quad:
            raise NonLinearError(
                "product would be degree three or higher; an objective may be "
                "quadratic, but no higher"
            )
        out = Quadratic(self.constant * other.constant)
        for k, v in self.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v * other.constant
        for k, v in other.terms.items():
            out.terms[k] = out.terms.get(k, 0.0) + v * self.constant
        for ka, va in self.terms.items():
            for kb, vb in other.terms.items():
                pair = (ka, kb) if ka <= kb else (kb, ka)
                out.quad[pair] = out.quad.get(pair, 0.0) + va * vb
        return out.prune()

    def prune(self, eps: float = 1e-12) -> Quadratic:
        self.terms = {k: v for k, v in self.terms.items() if abs(v) > eps}
        self.quad = {k: v for k, v in self.quad.items() if abs(v) > eps}
        return self


def var_key(name: str, index: list[str]) -> str:
    return f"{name}[{','.join(index)}]" if index else name


class Evaluator:
    """Evaluates IR expressions against a model and a binding environment."""

    def __init__(self, model: IRModel, on_var: callable | None = None):
        self.model = model
        self._sets = {s.name: s for s in model.sets}
        self._params = {p.name: p for p in model.parameters}
        self._vars = {v.name: v for v in model.variables}
        # Called with (IRVar, index tuple) the first time a variable key is
        # seen, so the compiler can materialise columns lazily instead of
        # enumerating a cross-product it may never use.
        self._on_var = on_var

    # ------------------------------------------------------------------ sets

    def elements(self, set_name: str) -> list[str]:
        s = self._sets.get(set_name)
        if s is None:
            raise DomainError(f"unknown set '{set_name}'")
        return s.elements

    def is_int_set(self, set_name: str) -> bool:
        s = self._sets.get(set_name)
        return s is not None and s.kind == "int"

    # ----------------------------------------------------------- evaluation

    def value(self, node, env: dict[str, tuple[str, str]]) -> Value:
        """Evaluate a constant expression. ``env`` maps index name -> (element, set)."""
        aff = self.affine(node, env, allow_vars=False)
        if isinstance(aff, str):
            return aff
        return aff.constant

    def affine(
        self,
        node,
        env: dict[str, tuple[str, str]],
        allow_vars: bool = True,
        max_degree: int = 1,
    ):
        """Fold ``node`` into an :class:`Affine` (or a ``str`` for label values).

        ``max_degree`` of 2 folds into a :class:`Quadratic` instead, which is
        what objectives are evaluated with. The walk below is identical either
        way — only ``mul`` behaves differently — so the constructor is chosen
        once here and the degree-one path allocates exactly what it did before.
        """
        op = node.op
        cls = Quadratic if max_degree > 1 else Affine

        if op == "const":
            return cls(float(node.value))

        if op == "lit":
            return node.value

        if op == "idx":
            bound = env.get(node.name)
            if bound is None:
                raise UnboundIndexError(f"index '{node.name}' is not bound here")
            element, set_name = bound
            if self.is_int_set(set_name):
                return cls(float(int(element)))
            return element

        if op == "param":
            idx = self._subscript(node.index, env, f"parameter '{node.name}'")
            param = self._params.get(node.name)
            if param is None:
                raise DomainError(f"unknown parameter '{node.name}'")
            self._check_arity(param.index_sets, idx, f"parameter '{node.name}'")
            return cls(param.get(tuple(idx)))

        if op == "var":
            if not allow_vars:
                raise NonLinearError(
                    f"decision variable '{node.name}' used where a constant is required"
                )
            decl = self._vars.get(node.name)
            if decl is None:
                raise DomainError(f"unknown variable '{node.name}'")
            idx = self._subscript(node.index, env, f"variable '{node.name}'")
            self._check_arity(decl.index_sets, idx, f"variable '{node.name}'")
            # _check_arity above guarantees equal length; strict keeps it that way.
            for set_name, element in zip(decl.index_sets, idx, strict=True):
                if element not in self.elements(set_name):
                    raise DomainError(
                        f"variable '{node.name}' subscripted with '{element}', "
                        f"which is not in set '{set_name}'"
                    )
            key = var_key(node.name, idx)
            if self._on_var is not None:
                self._on_var(decl, idx, key)
            return cls(0.0, {key: 1.0})

        if op == "neg":
            return -self._num(self.affine(node.arg, env, allow_vars, max_degree))

        if op == "add":
            total = cls()
            for a in node.args:
                total = total + self._num(self.affine(a, env, allow_vars, max_degree))
            return total

        if op == "sub":
            if not node.args:
                return cls()
            total = self._num(self.affine(node.args[0], env, allow_vars, max_degree))
            for a in node.args[1:]:
                total = total - self._num(self.affine(a, env, allow_vars, max_degree))
            return total

        if op == "mul":
            product = cls(1.0)
            for a in node.args:
                factor = self._num(self.affine(a, env, allow_vars, max_degree))
                if product.is_constant:
                    product = factor.scale(product.constant)
                elif factor.is_constant:
                    product = product.scale(factor.constant)
                elif max_degree > 1:
                    product = product.multiply(factor)
                else:
                    raise NonLinearError(
                        "product of two expressions that both contain decision "
                        "variables; a product of decisions is allowed in an "
                        "objective, but a constraint row has to stay linear"
                    )
            return product

        if op == "div":
            if len(node.args) != 2:
                raise NonLinearError("division takes exactly two operands")
            num = self._num(self.affine(node.args[0], env, allow_vars, max_degree))
            den = self._num(self.affine(node.args[1], env, allow_vars, max_degree))
            if not den.is_constant:
                raise NonLinearError("division by an expression containing decision variables")
            if den.constant == 0:
                raise DomainError("division by zero")
            return num.scale(1.0 / den.constant)

        if op == "sum":
            total = cls()
            for binding_env in self.iterate(node.over, env, node.where):
                total = total + self._num(
                    self.affine(node.body, binding_env, allow_vars, max_degree)
                )
            return total

        raise DomainError(f"unsupported expression node '{op}'")

    # ----------------------------------------------------------- iteration

    def iterate(self, bindings, env: dict, where=None):
        """Yield one environment per tuple of the binding cross-product."""
        if not bindings:
            if where is None or self.predicate(where, env):
                yield env
            return

        def walk(i: int, current: dict):
            if i == len(bindings):
                if where is None or self.predicate(where, current):
                    yield current
                return
            b = bindings[i]
            for element in self.elements(b.set):
                yield from walk(i + 1, {**current, b.index: (element, b.set)})

        yield from walk(0, env)

    def predicate(self, node, env: dict) -> bool:
        pred = node.pred
        if pred == "and":
            return all(self.predicate(a, env) for a in node.args)
        if pred == "or":
            return any(self.predicate(a, env) for a in node.args)
        if pred == "not":
            return not self.predicate(node.arg, env)
        if pred == "cmp":
            lhs = self.value(node.lhs, env)
            rhs = self.value(node.rhs, env)
            if isinstance(lhs, str) or isinstance(rhs, str):
                if node.op not in ("eq", "ne"):
                    raise DomainError(
                        f"cannot order labels {lhs!r} and {rhs!r}; only eq/ne apply"
                    )
                return (lhs == rhs) if node.op == "eq" else (lhs != rhs)
            return {
                "eq": lhs == rhs, "ne": lhs != rhs,
                "lt": lhs < rhs, "le": lhs <= rhs,
                "gt": lhs > rhs, "ge": lhs >= rhs,
            }[node.op]
        raise DomainError(f"unsupported predicate '{pred}'")

    # ------------------------------------------------------------- helpers

    def _subscript(self, index_exprs, env: dict, where: str) -> list[str]:
        """Resolve subscript expressions to element labels."""
        out: list[str] = []
        for e in index_exprs:
            v = self.value(e, env)
            out.append(v if isinstance(v, str) else self._as_element(v, where))
        return out

    @staticmethod
    def _as_element(v: float, where: str) -> str:
        if abs(v - round(v)) > 1e-9:
            raise DomainError(f"subscript {v} is not an integer element", where=where)
        return str(int(round(v)))

    @staticmethod
    def _check_arity(index_sets: list[str], idx: list[str], where: str) -> None:
        if len(index_sets) != len(idx):
            raise DomainError(
                f"{where} expects {len(index_sets)} subscript(s), got {len(idx)}"
            )

    @staticmethod
    def _num(v) -> Affine:
        if isinstance(v, str):
            raise DomainError(f"label {v!r} used in arithmetic")
        return v

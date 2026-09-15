"""The compiler is the part that must never be quietly wrong."""

from __future__ import annotations

import pytest

from psp.compiler import CompileError, NonLinearError, flatten
from psp.compiler.evaluator import Affine
from psp.ir import IRConstraint, IRModel, IRObjective, IRParam, IRSet, IRVar
from psp.ir.dsl import add, cmp, ge, i, le, mul, num, over, p, total, v


def tiny_model(**overrides) -> IRModel:
    return IRModel(
        name="tiny",
        sets=[IRSet(name="I", elements=["a", "b"])],
        parameters=[IRParam(name="c", index_sets=["I"], values={"a": 2.0, "b": 3.0})],
        variables=[IRVar(name="x", index_sets=["I"], kind="continuous", lb=0.0, ub=10.0)],
        constraints=[
            IRConstraint(
                name="cap",
                rel=le(total(v("x", i("k")), k="I"), num(5)),
            )
        ],
        objectives=[
            IRObjective(
                name="value", sense="maximize",
                expr=total(mul(p("c", i("k")), v("x", i("k"))), k="I"),
            )
        ],
        **overrides,
    )


def test_affine_folding_collapses_constants():
    a = Affine(1.0, {"x": 2.0})
    b = Affine(4.0, {"x": -2.0, "y": 1.0})
    combined = a + b
    assert combined.constant == 5.0
    # x cancels exactly and must not survive as a zero coefficient
    assert combined.terms == {"y": 1.0}


def test_flatten_produces_sparse_rows():
    flat = flatten(tiny_model())
    assert flat.stats() == {
        "variables": 2, "constraints": 1, "nonzeros": 2,
        "variable_kinds": {"continuous": 2}, "is_integer": False,
        "is_quadratic": False,
    }
    row = flat.constraints[0]
    assert row.terms == {"x[a]": 1.0, "x[b]": 1.0}
    assert row.op == "le" and row.rhs == 5.0


def test_maximisation_is_negated_internally_but_recorded_faithfully():
    flat = flatten(tiny_model())
    # The flat model always minimises, so a maximisation is negated...
    assert flat.objective.sense == "minimize"
    assert flat.objective.terms == {"x[a]": -2.0, "x[b]": -3.0}
    # ...but the component keeps the user's sense and sign, which is what gets
    # reported back. Losing this is how a report ends up showing -4500.
    component = flat.objective.components[0]
    assert component["sense"] == "maximize"
    assert component["terms"] == {"x[a]": 2.0, "x[b]": 3.0}


def test_forall_and_where_expand_deterministically():
    model = tiny_model()
    model.constraints.append(
        IRConstraint(
            name="only_a", forall=over(k="I"),
            where=cmp("eq", i("k"), "a"),
            rel=ge(v("x", i("k")), num(1)),
        )
    )
    flat = flatten(model)
    keys = [c.key for c in flat.constraints]
    assert keys == ["cap", "only_a[a]"]
    # Compiling twice must give byte-identical output, or runs are not reproducible.
    assert flatten(model).model_dump_json() == flat.model_dump_json()


def test_integer_set_indices_support_arithmetic():
    model = IRModel(
        name="shifted",
        sets=[IRSet.integers("T", 0, 3)],
        parameters=[IRParam(name="d", index_sets=[], values={"": 1.0})],
        variables=[IRVar(name="y", index_sets=["T"], kind="binary")],
        constraints=[
            IRConstraint(
                name="link", forall=over(t="T"),
                where=cmp("ge", i("t"), num(1)),
                # y[t] <= y[t - d], with the subscript computed from the index
                rel=le(v("y", i("t")), v("y", add(i("t"), mul(num(-1), p("d"))))),
            )
        ],
    )
    flat = flatten(model)
    assert [c.key for c in flat.constraints] == ["link[1]", "link[2]", "link[3]"]
    assert flat.constraints[0].terms == {"y[1]": 1.0, "y[0]": -1.0}


def test_quadratic_terms_are_rejected_not_silently_linearised():
    model = tiny_model()
    model.constraints.append(
        IRConstraint(name="bad", rel=le(mul(v("x", "a"), v("x", "b")), num(1)))
    )
    with pytest.raises(NonLinearError, match="both contain decision variables"):
        flatten(model)


def test_subscript_outside_its_set_is_rejected():
    model = tiny_model()
    model.constraints.append(IRConstraint(name="oops", rel=le(v("x", "zzz"), num(1))))
    with pytest.raises(CompileError, match="not in set 'I'"):
        flatten(model)


def test_constraint_with_no_variables_that_data_violates_fails_loudly():
    model = tiny_model()
    # 2 <= 1 is false for every assignment; solving would report a confusing
    # "infeasible" with no indication of which row is impossible.
    model.constraints.append(IRConstraint(name="impossible", rel=le(p("c", "a"), num(1))))
    with pytest.raises(CompileError, match="violated by the data"):
        flatten(model)


def test_constraint_with_no_variables_that_data_satisfies_is_dropped():
    model = tiny_model()
    model.constraints.append(IRConstraint(name="trivial", rel=ge(p("c", "a"), num(1))))
    assert [c.key for c in flatten(model).constraints] == ["cap"]


def test_fingerprint_changes_with_data_and_ignores_metadata():
    base = tiny_model()
    same = tiny_model()
    assert base.fingerprint() == same.fingerprint()

    same.metadata["note"] = "metadata must not affect the fingerprint"
    assert base.fingerprint() == same.fingerprint()

    changed = tiny_model()
    changed.parameters[0].values["a"] = 99.0
    assert changed.fingerprint() != base.fingerprint()

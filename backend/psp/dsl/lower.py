"""Parse tree -> Problem Model.

This is where a bare word finally becomes something: the index of an enclosing
``forall``, a parameter, a variable, or the name of a set element. The parser
cannot decide that, because the declaration that settles it may appear later in
the file; this pass has the whole program in hand.

A word that matches nothing is an error here rather than a puzzle three layers
down, and it comes with the closest name that would have worked.
"""

from __future__ import annotations

from psp.dsl import nodes
from psp.dsl.errors import ResolveError, did_you_mean
from psp.ir.expr import (
    Add,
    And,
    Cmp,
    Const,
    Div,
    IdxRef,
    Lit,
    Mul,
    Neg,
    Not,
    Or,
    ParamRef,
    Rel,
    Sub,
    Sum,
    VarRef,
)
from psp.ir.expr import Binding as IrBinding
from psp.problem.hierarchy import Hierarchy, HierarchyError, derive, shapes
from psp.problem.spec import (
    Assumption,
    ParameterValue,
    ProblemConstraint,
    ProblemObjective,
    ProblemParameter,
    ProblemSet,
    ProblemSpec,
    ProblemVariable,
    Scenario,
    ScenarioOverride,
    SourceRef,
    StructureHint,
)

CATEGORIES = {"physical", "policy", "regulatory", "operational", "modelling"}


class Lowering:
    def __init__(self, program: nodes.Program, source: str, origin: str):
        self.program = program
        self.source = source
        self.origin = origin
        self.sets: dict[str, nodes.SetDecl] = {}
        self.params: dict[str, nodes.ParamDecl] = {}
        self.vars: dict[str, nodes.VarDecl] = {}
        self.elements: dict[str, str] = {}  # element -> the set that declares it
        self.hierarchies: list[Hierarchy] = []
        self.derived: dict[str, str] = {}  # derived table name -> hierarchy name

    # ------------------------------------------------------------- errors

    def fail(self, message: str, node, hint: str | None = None) -> ResolveError:
        return ResolveError(message, getattr(node, "line", 0), getattr(node, "column", 0),
                            self.source, hint)

    # -------------------------------------------------------------- build

    def run(self) -> ProblemSpec:
        self._collect()
        spec = ProblemSpec(
            key=self.program.key,
            name=self.program.name,
            problem_type=self.program.key,
            description=self.program.description,
            template_key=None,
            metadata={"authored_in": "dsl"},
        )
        for decl in self.program.declarations:
            if isinstance(decl, nodes.SetDecl):
                spec.sets.append(self._set(decl))
            elif isinstance(decl, nodes.ParamDecl):
                spec.parameters.append(self._param(decl))
            elif isinstance(decl, nodes.VarDecl):
                spec.variables.append(self._var(decl))
            elif isinstance(decl, nodes.ConstraintDecl):
                spec.constraints.append(self._constraint(decl))
            elif isinstance(decl, nodes.ObjectiveDecl):
                spec.objectives.append(self._objective(decl))
            elif isinstance(decl, nodes.AssumeDecl):
                spec.assumptions.append(self._assumption(decl))
            elif isinstance(decl, nodes.ScenarioDecl):
                spec.scenarios.append(self._scenario(decl))
            elif isinstance(decl, nodes.StructureDecl):
                spec.structure = StructureHint(kind=decl.kind, **decl.fields)
        spec.hierarchies = list(self.hierarchies)
        for parameter in spec.parameters:
            parameter.derived_from = self.derived.get(parameter.name)
        if not spec.variables:
            raise ResolveError(
                f"problem '{spec.key}' declares no decision variables",
                hint="a problem with nothing to decide has nothing to solve; add a 'var' line",
            )
        return spec

    def _expand_hierarchies(self) -> None:
        """Turn each tree into the parameter declarations it stands for.

        Done before anything else reads the program, so every later pass — name
        resolution, arity checking, scenario overrides — sees ordinary tables
        and needs to know nothing about hierarchies.
        """
        elements: dict[str, list[str]] = {
            d.name: list(d.elements)
            for d in self.program.declarations
            if isinstance(d, nodes.SetDecl)
        }
        expanded: list = []
        for decl in self.program.declarations:
            expanded.append(decl)
            if not isinstance(decl, nodes.HierarchyDecl):
                continue
            if decl.name not in elements:
                raise self.fail(
                    f"hierarchy '{decl.name}' is not a declared set", decl,
                    hint=did_you_mean(decl.name, list(elements)),
                )
            seen: dict[str, str] = {}
            for child, parent in decl.parent:
                if child in seen:
                    raise self.fail(
                        f"'{child}' is given a parent twice in hierarchy "
                        f"'{decl.name}'", decl,
                    )
                seen[child] = parent
            tree = Hierarchy(
                name=decl.name, parent=seen,
                from_relationship=decl.from_relationship or None, **decl.derived,
            )
            if tree.from_relationship:
                # The edges arrive with the domain, but the tables they will
                # produce are already named — and a rule written against one has
                # to resolve now, before any database is involved. So the shapes
                # are declared here and binding fills them.
                tables = shapes(tree)
            else:
                try:
                    tables = derive(tree, elements[decl.name])
                except HierarchyError as exc:
                    raise self.fail(str(exc), decl) from exc
            self.hierarchies.append(tree)
            for table in tables:
                expanded.append(
                    nodes.ParamDecl(
                        line=decl.line, column=decl.column,
                        name=table["name"], index_sets=table["index_sets"],
                        default=table["default"], values=table["values"],
                        description=_derived_description(table["name"], tree),
                    )
                )
                self.derived[table["name"]] = tree.name
        self.program.declarations = expanded

    def _collect(self) -> None:
        """Index every declaration first, so order in the file does not matter."""
        self._expand_hierarchies()
        for decl in self.program.declarations:
            if isinstance(decl, nodes.SetDecl):
                self._claim(self.sets, decl.name, decl, "set")
                for element in decl.elements:
                    self.elements.setdefault(element, decl.name)
            elif isinstance(decl, nodes.ParamDecl):
                self._claim(self.params, decl.name, decl, "parameter")
            elif isinstance(decl, nodes.VarDecl):
                self._claim(self.vars, decl.name, decl, "variable")
        for name, decl in {**self.params, **self.vars}.items():
            for set_name in decl.index_sets:
                self._known_set(set_name, decl)
            if name in self.params and name in self.vars:
                raise self.fail(f"'{name}' is declared as both a parameter and a variable", decl)

    def _claim(self, registry: dict, name: str, decl, kind: str) -> None:
        if name in registry:
            raise self.fail(
                f"{kind} '{name}' is declared twice",
                decl,
                hint=f"the first is on line {registry[name].line}",
            )
        registry[name] = decl

    def _known_set(self, name: str, node) -> nodes.SetDecl:
        if name not in self.sets:
            raise self.fail(f"unknown set '{name}'", node,
                            hint=did_you_mean(name, self.sets))
        return self.sets[name]

    # -------------------------------------------------------- declarations

    def _set(self, decl: nodes.SetDecl) -> ProblemSet:
        if decl.kind == "int":
            for element in decl.elements:
                try:
                    int(element)
                except ValueError:
                    raise self.fail(
                        f"set '{decl.name}' is an int set, so '{element}' is not a valid element",
                        decl,
                        hint="int sets hold whole numbers, so the compiler can do arithmetic "
                             "on their indices",
                    ) from None
        for key in decl.labels:
            if key not in decl.elements:
                raise self.fail(f"set '{decl.name}' has no element '{key}' to label", decl,
                                hint=did_you_mean(key, decl.elements))
        return ProblemSet(
            name=decl.name, kind=decl.kind, elements=decl.elements,
            labels=decl.labels, entity_type=decl.entity_type,
            from_entity_type=decl.from_entity_type or None,
            description=decl.description,
        )

    def _param(self, decl: nodes.ParamDecl) -> ProblemParameter:
        if decl.from_attribute:
            return ProblemParameter(
                name=decl.name, index_sets=decl.index_sets, values=[],
                default=decl.default, unit=decl.unit, description=decl.description,
                from_attribute=decl.from_attribute,
            )
        values = []
        for index, value in decl.values:
            for position, element in enumerate(index):
                set_name = decl.index_sets[position]
                if element not in self.sets[set_name].elements:
                    raise self.fail(
                        f"'{element}' is not in set '{set_name}', which indexes "
                        f"parameter '{decl.name}'",
                        decl,
                        hint=did_you_mean(element, self.sets[set_name].elements),
                    )
            values.append(ParameterValue(index=index, value=value,
                                         origin=SourceRef(source=self.origin)))
        return ProblemParameter(
            name=decl.name, index_sets=decl.index_sets, values=values,
            default=decl.default, unit=decl.unit, description=decl.description,
        )

    def _var(self, decl: nodes.VarDecl) -> ProblemVariable:
        return ProblemVariable(
            name=decl.name, index_sets=decl.index_sets, kind=decl.kind,
            lb=decl.lb, ub=decl.ub, description=decl.description,
            decision_meaning=decl.meaning,
        )

    def _constraint(self, decl: nodes.ConstraintDecl) -> ProblemConstraint:
        if decl.category not in CATEGORIES:
            raise self.fail(
                f"'{decl.category}' is not a constraint category", decl,
                hint="use one of " + ", ".join(sorted(CATEGORIES)),
            )
        if decl.penalty is not None and decl.penalty <= 0:
            raise self.fail(
                f"a penalty of {decl.penalty:g} makes '{decl.name}' free to break",
                decl,
                hint="give a positive cost, or drop 'soft' to make the rule hard",
            )
        scope = {b.index: b.set_name for b in decl.forall}
        for binding in decl.forall:
            self._known_set(binding.set_name, binding)
        return ProblemConstraint(
            name=decl.name,
            statement=decl.statement,
            category=decl.category,
            penalty=decl.penalty,
            when=self._expression(decl.when, scope) if decl.when is not None else None,
            when_is=decl.when_is,
            rationale=decl.rationale,
            forall=[IrBinding(index=b.index, set=b.set_name) for b in decl.forall],
            where=self._predicate(decl.where, scope) if decl.where else None,
            rel=Rel(op=decl.op,
                    lhs=self._expression(decl.lhs, scope),
                    rhs=self._expression(decl.rhs, scope)),
        )

    def _objective(self, decl: nodes.ObjectiveDecl) -> ProblemObjective:
        return ProblemObjective(
            name=decl.name, statement=decl.statement, sense=decl.sense,
            weight=decl.weight, unit=decl.unit, expr=self._expression(decl.expr, {}),
        )

    def _assumption(self, decl: nodes.AssumeDecl) -> Assumption:
        return Assumption(key=decl.key, statement=decl.statement,
                          rationale=decl.rationale, affects=decl.affects)

    def _scenario(self, decl: nodes.ScenarioDecl) -> Scenario:
        overrides: list[ScenarioOverride] = []
        for override in decl.overrides:
            if override.parameter not in self.params:
                raise self.fail(f"unknown parameter '{override.parameter}'", override,
                                hint=did_you_mean(override.parameter, self.params))
            param = self.params[override.parameter]
            if override.index is None:
                # No subscript means every value the parameter declares — the
                # usual case, since "demand up 30%" means all of it.
                if not param.values:
                    # A parameter that is only a default has nothing to scale,
                    # and silently scaling nothing is how a scenario ends up
                    # reporting no change and nobody noticing.
                    raise self.fail(
                        f"'{override.parameter}' declares no values, so there is nothing "
                        "for this scenario to change",
                        override,
                        hint="give the parameter explicit values, or name the index to "
                             f"change: {override.parameter}[...]",
                    )
                targets = [index for index, _ in param.values]
            else:
                if len(override.index) != len(param.index_sets):
                    raise self.fail(
                        f"'{override.parameter}' is indexed by {len(param.index_sets)} "
                        f"set(s), but this override names {len(override.index)}",
                        override,
                    )
                targets = [override.index]
            for index in targets:
                overrides.append(ScenarioOverride(
                    parameter=override.parameter, index=index,
                    value=override.value, scale=override.scale,
                ))
        return Scenario(key=decl.key, name=decl.name,
                        description=decl.description, overrides=overrides)

    # --------------------------------------------------------- expressions

    def _expression(self, node, scope: dict[str, str]):
        if isinstance(node, nodes.Num):
            return Const(value=node.value)
        if isinstance(node, nodes.Text):
            return Lit(value=node.value)
        if isinstance(node, nodes.Word):
            return self._word(node, scope)
        if isinstance(node, nodes.Lookup):
            return self._lookup(node, scope)
        if isinstance(node, nodes.Unary):
            return Neg(arg=self._expression(node.arg, scope))
        if isinstance(node, nodes.Arith):
            args = [self._expression(a, scope) for a in node.args]
            if node.op == "/":
                if len(args) != 2:
                    raise self.fail("division takes exactly two operands", node)
                return Div(args=args)
            return {"+": Add, "-": Sub, "*": Mul}[node.op](args=args)
        if isinstance(node, nodes.Aggregate):
            inner = dict(scope)
            for binding in node.bindings:
                self._known_set(binding.set_name, binding)
                inner[binding.index] = binding.set_name
            return Sum(
                over=[IrBinding(index=b.index, set=b.set_name) for b in node.bindings],
                body=self._expression(node.body, inner),
                where=self._predicate(node.where, inner) if node.where else None,
            )
        raise self.fail(f"cannot read {type(node).__name__} as a value", node)

    def _word(self, node: nodes.Word, scope: dict[str, str]):
        if node.name in scope:
            return IdxRef(name=node.name)
        if node.name in self.params:
            declared = self.params[node.name]
            if declared.index_sets:
                raise self.fail(
                    f"parameter '{node.name}' is indexed by "
                    f"{', '.join(declared.index_sets)} and needs a subscript",
                    node,
                    hint=f"write {node.name}[{', '.join(declared.index_sets).lower()}]",
                )
            return ParamRef(name=node.name)
        if node.name in self.vars:
            declared = self.vars[node.name]
            if declared.index_sets:
                raise self.fail(
                    f"variable '{node.name}' is indexed by "
                    f"{', '.join(declared.index_sets)} and needs a subscript",
                    node,
                )
            return VarRef(name=node.name)
        if node.name in self.elements:
            return Lit(value=node.name)
        raise self.fail(
            f"'{node.name}' is not an index, a parameter, a variable or a set element",
            node,
            hint=did_you_mean(
                node.name,
                [*scope, *self.params, *self.vars, *self.elements],
            ),
        )

    def _lookup(self, node: nodes.Lookup, scope: dict[str, str]):
        args = [self._expression(arg, scope) for arg in node.args]
        if node.name in self.params:
            declared, build = self.params[node.name], ParamRef
            kind = "parameter"
        elif node.name in self.vars:
            declared, build = self.vars[node.name], VarRef
            kind = "variable"
        else:
            raise self.fail(
                f"'{node.name}' is not a declared parameter or variable", node,
                hint=did_you_mean(node.name, [*self.params, *self.vars]),
            )
        if len(declared.index_sets) != len(args):
            raise self.fail(
                f"{kind} '{node.name}' takes {len(declared.index_sets)} subscript(s), "
                f"given {len(args)}",
                node,
                hint=(f"it is indexed by {', '.join(declared.index_sets)}"
                      if declared.index_sets else "it takes no subscript"),
            )
        return build(name=node.name, index=args)

    # ---------------------------------------------------------- predicates

    def _predicate(self, node, scope: dict[str, str]):
        if isinstance(node, nodes.Compare):
            return Cmp(op=node.op,
                       lhs=self._expression(node.lhs, scope),
                       rhs=self._expression(node.rhs, scope))
        if isinstance(node, nodes.Logical):
            args = [self._predicate(a, scope) for a in node.args]
            return And(args=args) if node.op == "and" else Or(args=args)
        if isinstance(node, nodes.Negate):
            return Not(arg=self._predicate(node.arg, scope))
        raise self.fail(f"cannot read {type(node).__name__} as a filter", node)


def lower(program: nodes.Program, source: str = "", origin: str = "dsl") -> ProblemSpec:
    return Lowering(program, source, origin).run()


def _derived_description(name: str, tree: Hierarchy) -> str:
    """What the table is, in words, so the Problem screen and any explanation
    citing it still read as though someone had written them."""
    what = {
        tree.covers: f"1 when the first node of {tree.name} covers the second",
        tree.overlap: f"Leaf nodes of {tree.name} that two nodes have in common",
        tree.leaf: f"1 for a node of {tree.name} with nothing beneath it",
        tree.count: f"How many leaf nodes {tree.name} has",
        tree.depth: f"Steps from the root of {tree.name}",
    }[name]
    return f"{what}, derived from the hierarchy"

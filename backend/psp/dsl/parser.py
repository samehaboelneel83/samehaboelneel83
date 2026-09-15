"""Recursive-descent parser for the problem language.

Hand-written rather than generated, for one reason: the error messages. A
parser generator would report "syntax error at token 47"; this one can say
"a constraint needs a relation (<=, >= or ==)" and point at the line.
"""

from __future__ import annotations

from typing import Any

from psp.dsl.errors import ParseError
from psp.dsl.lexer import Token, tokenize
from psp.dsl.nodes import (
    Aggregate,
    Arith,
    AssumeDecl,
    Binding,
    Compare,
    ConstraintDecl,
    HierarchyDecl,
    Logical,
    Lookup,
    Negate,
    Num,
    ObjectiveDecl,
    Override,
    ParamDecl,
    Program,
    ScenarioDecl,
    SetDecl,
    StructureDecl,
    Text,
    Unary,
    VarDecl,
    Word,
)

RELATIONS = {"<=": "le", ">=": "ge", "==": "eq"}
COMPARISONS = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
VAR_KINDS = {"binary", "integer", "continuous"}


class Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens: list[Token] = tokenize(text)
        self.pos = 0

    # --------------------------------------------------------- token access

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def at(self, kind: str, value: str | None = None) -> bool:
        token = self.current
        return token.kind == kind and (value is None or token.value == value)

    def at_any(self, kind: str, values) -> bool:
        return self.current.kind == kind and self.current.value in values

    def advance(self) -> Token:
        token = self.current
        if token.kind != "end":
            self.pos += 1
        return token

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        return self.advance() if self.at(kind, value) else None

    def expect(self, kind: str, value: str | None = None, what: str | None = None) -> Token:
        if self.at(kind, value):
            return self.advance()
        wanted = what or (f"'{value}'" if value else kind)
        found = "end of file" if self.current.kind == "end" else (
            "end of line" if self.current.kind == "newline" else f"'{self.current.value}'"
        )
        raise self.error(f"expected {wanted}, found {found}")

    def identifier(self, what: str) -> str:
        """Accept a name in any of the forms a name can take.

        A keyword is fine ('set' is a reasonable parameter name), and so is a
        quoted string — which is how a name containing a hyphen or a space gets
        through, since bare ``export-me`` would lex as a subtraction. The writer
        quotes exactly those, so anything it emits can be read back.
        """
        token = self.current
        if token.kind in ("name", "keyword", "string"):
            self.advance()
            return token.value
        raise self.error(f"expected {what}, found '{token.value or 'end of file'}'")

    def error(
        self, message: str, hint: str | None = None, token: Token | None = None
    ) -> ParseError:
        token = token or self.current
        return ParseError(message, token.line, token.column, self.text, hint)

    def string_value(self, what: str) -> str:
        """Read a string, joining any that continue on following lines.

        Statements and rationales are prose, and prose does not fit in eighty
        columns. Adjacent strings concatenate, so a long sentence can be broken
        wherever it reads best."""
        parts = [self.expect("string", what=what).value]
        while True:
            mark = self.pos
            # Only across a line break. Two strings on one line are two
            # different fields — `unit "hours" "how long it takes"` — and
            # joining those would silently merge them.
            if not self.at("newline"):
                break
            self.advance()
            if self.at("string"):
                parts.append(self.advance().value)
            else:
                self.pos = mark
                break
        return "".join(parts)

    def continues_with(
        self, keywords: set[str], strings: bool = False, symbols: set[str] | None = None
    ) -> bool:
        """True when the next line carries one of this declaration's attributes.

        Attributes read better under the thing they describe once a declaration
        grows past a line, which is most of the time. Only the listed keywords
        continue a declaration, so the next ``set`` or ``constraint`` still
        starts a new one.
        """
        mark = self.pos
        while self.at("newline"):
            self.advance()
        if (
            self.at_any("keyword", keywords)
            or (strings and self.at("string"))
            or (symbols and self.at_any("symbol", symbols))
        ):
            return True
        self.pos = mark
        return False

    def trailing_description(self) -> str | None:
        """A bare string on the line after a declaration describes it.

        Unambiguous, because no declaration begins with a string."""
        mark = self.pos
        if self.at("newline"):
            self.advance()
        if self.at("string"):
            text = self.string_value("a description")
            self.end_of_declaration()
            return text
        self.pos = mark
        return None

    def skip_newlines(self) -> None:
        while self.at("newline"):
            self.advance()

    def end_of_declaration(self) -> None:
        if self.at("end"):
            return
        self.expect("newline", what="end of line")

    # ------------------------------------------------------------- program

    def parse(self) -> Program:
        self.skip_newlines()
        self.expect("keyword", "problem", what="the file to start with 'problem'")
        key = self.identifier("a problem key")
        name = self.accept("string")
        program = Program(
            key=key, name=name.value if name else key, line=1, column=1,
        )
        self.end_of_declaration()
        self.skip_newlines()

        # A bare string on its own line after the header is the description.
        if self.at("string"):
            program.description = self.string_value("a description")
            self.end_of_declaration()

        while not self.at("end"):
            self.skip_newlines()
            if self.at("end"):
                break
            program.declarations.append(self.declaration())
        return program

    def declaration(self) -> Any:
        token = self.current
        if token.kind != "keyword":
            raise self.error(
                f"expected a declaration, found '{token.value}'",
                hint="declarations start with set, param, var, constraint, minimize, "
                     "maximize, assume, scenario or structure",
            )
        handler = {
            "set": self.set_declaration,
            "param": self.param_declaration,
            "var": self.var_declaration,
            "constraint": self.constraint_declaration,
            "minimize": self.objective_declaration,
            "maximize": self.objective_declaration,
            "assume": self.assume_declaration,
            "scenario": self.scenario_declaration,
            "structure": self.structure_declaration,
            "hierarchy": self.hierarchy_declaration,
        }.get(token.value)
        if handler is None:
            raise self.error(
                f"'{token.value}' does not start a declaration",
                hint="declarations start with set, param, var, constraint, minimize, "
                     "maximize, assume, scenario or structure",
            )
        return handler()

    # ----------------------------------------------------------------- set

    def set_declaration(self) -> SetDecl:
        start = self.expect("keyword", "set")
        node = SetDecl(line=start.line, column=start.column)
        node.name = self.identifier("a set name")
        if self.accept("symbol", ":"):
            kind = self.identifier("'int' or 'label'")
            if kind not in ("int", "label"):
                raise self.error(f"a set is 'int' or 'label', not '{kind}'")
            node.kind = kind
        self.expect("symbol", "=", what="'=' and the set's elements")

        if self.at("number") and self.tokens[self.pos + 1].value == "..":
            low = int(float(self.advance().value))
            self.advance()
            high = int(float(self.expect("number", what="the end of the range").value))
            if high < low:
                raise self.error(f"range {low}..{high} runs backwards")
            node.elements = [str(v) for v in range(low, high + 1)]
            node.kind = "int"
        else:
            node.elements = self.element_list()

        while True:
            self.continues_with({"labels", "of"}, strings=True)
            if self.accept("keyword", "labels"):
                node.labels = self.label_map()
            elif self.accept("keyword", "of"):
                node.entity_type = self.identifier("an entity type")
            elif self.at("string"):
                node.description = self.string_value("a description")
            else:
                break
        self.end_of_declaration()
        node.description = node.description or self.trailing_description()
        return node

    def element_list(self) -> list[str]:
        elements = [self.element()]
        while self.accept("symbol", ","):
            elements.append(self.element())
        return elements

    def element(self) -> str:
        if self.at("string"):
            return self.advance().value
        if self.at("number"):
            return self.advance().value
        return self.identifier("a set element")

    def label_map(self) -> dict[str, str]:
        self.expect("symbol", "{", what="'{' and the labels")
        labels: dict[str, str] = {}
        self.skip_newlines()
        while not self.at("symbol", "}"):
            key = self.element()
            self.expect("symbol", ":", what="':' after the element")
            labels[key] = self.string_value("the label, in quotes")
            self.skip_newlines()
            if not self.accept("symbol", ","):
                break
            self.skip_newlines()
        self.expect("symbol", "}", what="'}' to close the labels")
        return labels

    # --------------------------------------------------------------- param

    def param_declaration(self) -> ParamDecl:
        start = self.expect("keyword", "param")
        node = ParamDecl(line=start.line, column=start.column)
        node.name = self.identifier("a parameter name")
        node.index_sets = self.optional_index_sets()

        while True:
            self.continues_with({"default", "unit"}, strings=True)
            if self.accept("keyword", "default"):
                node.default = self.number()
            elif self.accept("keyword", "unit"):
                node.unit = self.string_value("the unit, in quotes")
            elif self.at("string"):
                node.description = self.string_value("a description")
            else:
                break

        if self.accept("symbol", "="):
            if self.at("symbol", "{"):
                node.values = self.value_map(node)
            else:
                if node.index_sets:
                    raise self.error(
                        f"'{node.name}' is indexed, so its values need braces",
                        hint="write: = { (a, b): 1, (a, c): 2 }",
                    )
                node.values = [([], self.number())]
        self.end_of_declaration()
        node.description = node.description or self.trailing_description()
        return node

    def optional_index_sets(self) -> list[str]:
        if not self.accept("symbol", "["):
            return []
        sets = [self.identifier("a set name")]
        while self.accept("symbol", ","):
            sets.append(self.identifier("a set name"))
        self.expect("symbol", "]", what="']' to close the index sets")
        return sets

    def value_map(self, node: ParamDecl) -> list[tuple[list[str], float]]:
        self.expect("symbol", "{")
        values: list[tuple[list[str], float]] = []
        self.skip_newlines()
        while not self.at("symbol", "}"):
            key = self.value_key()
            if len(key) != len(node.index_sets):
                raise self.error(
                    f"'{node.name}' is indexed by {len(node.index_sets)} set(s) but this key "
                    f"has {len(key)}",
                    hint=(f"expected a key like ({', '.join(node.index_sets)})"
                          if node.index_sets else "this parameter takes no index"),
                )
            self.expect("symbol", ":", what="':' after the key")
            values.append((key, self.number()))
            self.skip_newlines()
            if not self.accept("symbol", ","):
                break
            self.skip_newlines()
        self.expect("symbol", "}", what="'}' to close the values")
        return values

    def value_key(self) -> list[str]:
        if self.accept("symbol", "("):
            key = [self.element()]
            while self.accept("symbol", ","):
                key.append(self.element())
            self.expect("symbol", ")", what="')' to close the key")
            return key
        return [self.element()]

    def number(self) -> float:
        negative = bool(self.accept("symbol", "-"))
        if self.accept("keyword", "inf"):
            return float("inf") * (-1 if negative else 1)
        token = self.expect("number", what="a number")
        return float(token.value) * (-1 if negative else 1)

    # ----------------------------------------------------------- hierarchy

    #: What may be derived from a tree, and the clause that names each. Matched
    #: as ordinary words rather than reserved as keywords, so a model is still
    #: free to call something 'covers'.
    DERIVABLE = ("covers", "overlap", "leaf", "count", "depth")

    def hierarchy_declaration(self) -> HierarchyDecl:
        start = self.expect("keyword", "hierarchy")
        node = HierarchyDecl(line=start.line, column=start.column)
        node.name = self.identifier("the set the tree is over")

        direction = self.identifier("'by parent'")
        if direction != "by":
            raise self.error(
                f"expected 'by parent', found '{direction}'",
                hint="a hierarchy says which way its map runs: 'by parent'",
            )
        node.direction = self.identifier("'parent'")
        if node.direction != "parent":
            raise self.error(
                f"a hierarchy is given 'by parent', not by '{node.direction}'",
                hint="write the map as child: parent",
            )
        self.skip_newlines()

        while self.at_any("name", self.DERIVABLE):
            clause = self.identifier("a derived table")
            if clause in node.derived:
                raise self.error(f"'{clause}' is named twice for this hierarchy")
            node.derived[clause] = self.identifier(f"a name for the {clause} table")
            self.skip_newlines()

        if not node.derived:
            raise self.error(
                f"hierarchy '{node.name}' derives nothing",
                hint="name at least one of " + ", ".join(self.DERIVABLE),
            )

        self.expect("symbol", "=", what="'=' and the child: parent map")
        self.expect("symbol", "{", what="the map in braces")
        self.skip_newlines()
        while not self.at("symbol", "}"):
            child = self.element()
            self.expect("symbol", ":", what="':' and the parent")
            node.parent.append((child, self.element()))
            self.skip_newlines()
            if not self.accept("symbol", ","):
                break
            self.skip_newlines()
        self.expect("symbol", "}", what="the map to close with '}'")
        self.end_of_declaration()
        return node

    # ----------------------------------------------------------------- var

    def var_declaration(self) -> VarDecl:
        start = self.expect("keyword", "var")
        node = VarDecl(line=start.line, column=start.column)
        node.name = self.identifier("a variable name")
        node.index_sets = self.optional_index_sets()

        if self.at_any("keyword", VAR_KINDS):
            node.kind = self.advance().value
        else:
            raise self.error(
                "a variable needs a kind",
                hint="write binary, integer or continuous after the name",
            )
        if node.kind == "binary":
            node.ub = 1.0

        while True:
            self.continues_with({"in", "means"}, strings=True)
            if self.accept("keyword", "in"):
                self.expect("symbol", "[", what="'[' and the bounds")
                node.lb = self.number()
                self.expect("symbol", ",", what="',' between the bounds")
                upper = self.number()
                node.ub = None if upper == float("inf") else upper
                self.expect("symbol", "]", what="']' to close the bounds")
            elif self.accept("keyword", "means"):
                node.meaning = self.string_value("what the decision means")
            elif self.at("string"):
                node.description = self.string_value("a description")
            else:
                break
        self.end_of_declaration()
        node.description = node.description or self.trailing_description()
        return node

    # ---------------------------------------------------------- constraint

    def constraint_declaration(self) -> ConstraintDecl:
        start = self.expect("keyword", "constraint")
        node = ConstraintDecl(line=start.line, column=start.column)
        node.name = self.identifier("a constraint name")
        if self.at("string"):
            node.statement = self.string_value("what the constraint means")
        self.skip_newlines()

        while True:
            if self.accept("keyword", "category"):
                node.category = self.identifier("a category")
                self.skip_newlines()
            elif self.accept("keyword", "soft"):
                self.expect("keyword", "penalty")
                node.penalty = self.number()
                self.skip_newlines()
            elif self.accept("keyword", "because"):
                node.rationale = self.string_value("the reason, in quotes")
                self.skip_newlines()
            else:
                break

        if self.accept("keyword", "forall"):
            node.forall = self.bindings()
            if self.accept("keyword", "where"):
                node.where = self.predicate()
            self.expect("symbol", ":", what="':' after the quantifier")
            self.skip_newlines()
        elif self.accept("keyword", "where"):
            node.where = self.predicate()
            self.expect("symbol", ":", what="':' after the filter")
            self.skip_newlines()

        if self.counted_body(node):
            if not node.statement:
                raise self.error(
                    f"constraint '{node.name}' has no statement",
                    hint='put what it means in quotes after the name, so an '
                         'explanation can cite it',
                    token=self.tokens[self.pos - 1],
                )
            self.end_of_declaration()
            return node

        node.lhs = self.expression()
        self.continues_with(set(), symbols=set(RELATIONS))
        if not self.at_any("symbol", RELATIONS):
            raise self.error(
                "a constraint needs a relation",
                hint="use <=, >= or == between the two sides",
            )
        node.op = RELATIONS[self.advance().value]
        node.rhs = self.expression()
        if not node.statement:
            raise self.error(
                f"constraint '{node.name}' has no statement",
                hint='put what it means in quotes after the name, so an explanation can cite it',
                token=self.tokens[self.pos - 1],
            )
        self.end_of_declaration()
        return node

    #: How many of a thing there may be, and the relation each phrase means.
    COUNTS = {"most": "le", "least": "ge"}

    def counted_body(self, node: ConstraintDecl) -> bool:
        """Parse ``never ...`` or ``at most N of ...``, if that is what is here.

        Both are the shapes that recur across the models in this repository —
        a sum of things that must not happen, and a sum held against a plain
        number. They build exactly the relation the long form builds; what they
        add is that the rule says what kind of rule it is, instead of leaving a
        reader to infer it from a zero on the right-hand side.
        """
        if self.at_any("name", ("never",)):
            self.advance()
            node.op, node.rhs = "eq", Num(line=0, column=0, value=0.0)
            node.lhs = self.counted_sum()
            return True

        if not self.at_any("name", ("at", "exactly")):
            return False
        word = self.advance().value
        if word == "exactly":
            node.op = "eq"
        else:
            which = self.identifier("'most' or 'least'")
            if which not in self.COUNTS:
                raise self.error(
                    f"expected 'at most' or 'at least', found 'at {which}'",
                )
            node.op = self.COUNTS[which]
        limit = self.current
        node.rhs = Num(line=limit.line, column=limit.column, value=self.number())
        self.expect("keyword", "of", what="'of' and what is being counted")
        node.lhs = self.counted_sum()
        return True

    def counted_sum(self) -> Aggregate:
        """``<expr> for <bindings> [where <predicate>]`` — a sum without its
        parentheses, since the phrase in front of it already says what it is."""
        node = Aggregate(line=self.current.line, column=self.current.column)
        node.body = self.expression()
        # Unlike the long form, this one is not inside brackets, so a newline
        # between the parts is a real token. Both continue the phrase.
        self.continues_with({"for"})
        self.expect("keyword", "for", what="'for' and what to count over")
        node.bindings = self.bindings()
        self.continues_with({"where"})
        if self.accept("keyword", "where"):
            node.where = self.predicate()
        return node

    def bindings(self) -> list[Binding]:
        result = [self.binding()]
        while self.accept("symbol", ","):
            result.append(self.binding())
        return result

    def binding(self) -> Binding:
        token = self.current
        index = self.identifier("an index name")
        self.expect("keyword", "in", what="'in' and the set to range over")
        return Binding(line=token.line, column=token.column,
                       index=index, set_name=self.identifier("a set name"))

    # ----------------------------------------------------------- objective

    def objective_declaration(self) -> ObjectiveDecl:
        start = self.advance()
        node = ObjectiveDecl(line=start.line, column=start.column, sense=start.value)
        node.name = self.identifier("an objective name")
        if self.at("string"):
            node.statement = self.string_value("what the objective means")
        while True:
            self.continues_with({"unit", "weight"})
            if self.accept("keyword", "unit"):
                node.unit = self.string_value("the unit, in quotes")
            elif self.accept("keyword", "weight"):
                node.weight = self.number()
            else:
                break
        self.expect("symbol", ":", what="':' before the expression")
        self.skip_newlines()
        node.expr = self.expression()
        if not node.statement:
            node.statement = f"{start.value.capitalize()} {node.name}."
        self.end_of_declaration()
        return node

    # -------------------------------------------------------------- assume

    def assume_declaration(self) -> AssumeDecl:
        start = self.expect("keyword", "assume")
        node = AssumeDecl(line=start.line, column=start.column)
        node.key = self.identifier("an assumption key")
        node.statement = self.string_value("the assumption, in quotes")
        self.skip_newlines()
        while True:
            if self.accept("keyword", "because"):
                node.rationale = self.string_value("the reason, in quotes")
                self.skip_newlines()
            elif self.accept("keyword", "affects"):
                node.affects = [self.identifier("a name")]
                while self.accept("symbol", ","):
                    node.affects.append(self.identifier("a name"))
                self.skip_newlines()
            else:
                break
        return node

    # ------------------------------------------------------------ scenario

    def scenario_declaration(self) -> ScenarioDecl:
        start = self.expect("keyword", "scenario")
        node = ScenarioDecl(line=start.line, column=start.column)
        node.key = self.identifier("a scenario key")
        node.name = self.advance().value if self.at("string") else node.key
        self.end_of_declaration()
        self.skip_newlines()
        if self.at("string"):
            node.description = self.string_value("a description")
            self.end_of_declaration()
            self.skip_newlines()

        while self.at_any("keyword", {"scale", "set"}):
            verb = self.advance().value
            override = Override(line=self.current.line, column=self.current.column)
            override.parameter = self.identifier("a parameter name")
            if self.accept("symbol", "["):
                override.index = [self.element()]
                while self.accept("symbol", ","):
                    override.index.append(self.element())
                self.expect("symbol", "]", what="']' to close the index")
            if verb == "scale":
                self.expect("keyword", "by", what="'by' and the factor")
                override.scale = self.number()
            else:
                self.expect("keyword", "to", what="'to' and the new value")
                override.value = self.number()
            node.overrides.append(override)
            self.end_of_declaration()
            self.skip_newlines()

        if not node.overrides:
            raise self.error(f"scenario '{node.key}' changes nothing",
                             hint="add a 'scale <param> by <n>' or 'set <param> to <n>' line")
        return node

    # ----------------------------------------------------------- structure

    def structure_declaration(self) -> StructureDecl:
        start = self.expect("keyword", "structure")
        node = StructureDecl(line=start.line, column=start.column)
        node.kind = self.identifier("a structure kind")
        self.end_of_declaration()
        self.skip_newlines()
        while self.at("name") and self.tokens[self.pos + 1].kind in ("name", "keyword"):
            field_name = self.identifier("a field name")
            node.fields[field_name] = self.identifier("a value")
            self.end_of_declaration()
            self.skip_newlines()
        return node

    # --------------------------------------------------------- expressions

    def expression(self) -> Any:
        return self._chain(self.term, {"+", "-"})

    def term(self) -> Any:
        return self._chain(self.factor, {"*", "/"})

    def _chain(self, operand, operators: set[str]) -> Any:
        """Parse a left-associative chain, collecting runs of one operator.

        ``a + b + c`` becomes a single three-argument node; ``a + b - c``
        becomes ``(a + b) - c``, which is what left-associativity means.
        Division never collects, since it takes exactly two operands."""
        node = operand()
        while self.at_any("symbol", operators):
            token = self.advance()
            rhs = operand()
            if (
                isinstance(node, Arith)
                and node.op == token.value
                and token.value != "/"
                and not node.grouped
            ):
                node.args.append(rhs)
            else:
                node = Arith(line=token.line, column=token.column,
                             op=token.value, args=[node, rhs])
        return node

    def factor(self) -> Any:
        if self.at("symbol", "-"):
            token = self.advance()
            return Unary(line=token.line, column=token.column, op="-", arg=self.factor())
        return self.atom()

    def atom(self) -> Any:
        token = self.current
        if self.at("number"):
            self.advance()
            return Num(line=token.line, column=token.column, value=float(token.value))
        if self.at("string"):
            self.advance()
            return Text(line=token.line, column=token.column, value=token.value)
        if self.at("keyword", "sum"):
            return self.aggregate()
        if self.accept("symbol", "("):
            inner = self.expression()
            self.expect("symbol", ")", what="')' to close the group")
            if isinstance(inner, Arith):
                inner.grouped = True
            return inner
        if token.kind in ("name", "keyword"):
            self.advance()
            if self.at("symbol", "["):
                self.advance()
                args = [self.expression()]
                while self.accept("symbol", ","):
                    args.append(self.expression())
                self.expect("symbol", "]", what="']' to close the subscript")
                return Lookup(line=token.line, column=token.column, name=token.value, args=args)
            return Word(line=token.line, column=token.column, name=token.value)
        raise self.error(f"expected a value, found '{token.value or 'end of file'}'")

    def aggregate(self) -> Aggregate:
        start = self.expect("keyword", "sum")
        self.expect("symbol", "(", what="'(' after sum")
        node = Aggregate(line=start.line, column=start.column)
        node.body = self.expression()
        self.expect("keyword", "for", what="'for' and what to sum over")
        node.bindings = self.bindings()
        if self.accept("keyword", "where"):
            node.where = self.predicate()
        self.expect("symbol", ")", what="')' to close the sum")
        return node

    # ---------------------------------------------------------- predicates

    def predicate(self) -> Any:
        return self._logical_chain(self.predicate_and, "or")

    def predicate_and(self) -> Any:
        return self._logical_chain(self.predicate_not, "and")

    def _logical_chain(self, operand, keyword: str) -> Any:
        """Collect a run of one connective into a single node.

        ``a and b and c`` is one three-part filter, which is what the Python
        builders produce; and/or are associative, so flattening changes nothing
        but the shape — and the shape is what a round trip compares."""
        node = operand()
        while self.at("keyword", keyword):
            token = self.advance()
            rhs = operand()
            if isinstance(node, Logical) and node.op == keyword and not node.grouped:
                node.args.append(rhs)
            else:
                node = Logical(line=token.line, column=token.column,
                               op=keyword, args=[node, rhs])
        return node

    def predicate_not(self) -> Any:
        if self.at("keyword", "not"):
            token = self.advance()
            return Negate(line=token.line, column=token.column, arg=self.predicate_not())
        # A parenthesised group is only a nested predicate if it contains one;
        # '(a + b) < c' starts with a bracket too.
        if self.at("symbol", "(") and self._group_is_predicate():
            self.advance()
            inner = self.predicate()
            self.expect("symbol", ")", what="')' to close the group")
            if isinstance(inner, Logical):
                inner.grouped = True
            return inner
        return self.comparison()

    def _group_is_predicate(self) -> bool:
        depth, i = 0, self.pos
        while i < len(self.tokens):
            token = self.tokens[i]
            if token.kind == "symbol" and token.value in "([":
                depth += 1
            elif token.kind == "symbol" and token.value in ")]":
                depth -= 1
                if depth == 0:
                    return False
            elif depth == 1 and (
                (token.kind == "keyword" and token.value in ("and", "or", "not"))
                or (token.kind == "symbol" and token.value in COMPARISONS)
            ):
                return True
            i += 1
        return False

    def comparison(self) -> Compare:
        lhs = self.expression()
        if not self.at_any("symbol", COMPARISONS):
            raise self.error(
                "expected a comparison",
                hint="a filter compares two things with ==, !=, <, <=, > or >=",
            )
        token = self.advance()
        return Compare(line=token.line, column=token.column,
                       op=COMPARISONS[token.value], lhs=lhs, rhs=self.expression())


def parse(text: str) -> Program:
    return Parser(text).parse()

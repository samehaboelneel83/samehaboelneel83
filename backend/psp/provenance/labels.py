"""Rendering keys the way a person reads them.

A key like ``place[algo_y2,hall_a,23]`` is exactly right for the compiler and
useless to the person reading the timetable. The sets that need it carry a
label map, and this module turns a key into ``place[algo_y2, hall_a, Thu
13:45]`` using it.

Labels are presentation only. The raw key stays the identifier everywhere it
matters — in ``Solution.values``, in provenance rows, and in the explain API —
so nothing downstream has to parse a display string back into an index.
"""

from __future__ import annotations

from psp.ir.model import IRModel


class LabelResolver:
    """Renders variable and constraint keys using the sets' label maps."""

    def __init__(self, model: IRModel):
        self._labels = {s.name: s.labels for s in model.sets if s.labels}
        self._variable_sets = {v.name: v.index_sets for v in model.variables}
        self._constraint_sets = {
            c.name: [b.set for b in c.forall] for c in model.constraints
        }

    @property
    def active(self) -> bool:
        """False when no set declares labels, so callers can skip the work."""
        return bool(self._labels)

    def element(self, set_name: str, element: str) -> str:
        return self._labels.get(set_name, {}).get(element, element)

    def variable(self, name: str, index: list[str]) -> str | None:
        return self._render(name, index, self._variable_sets.get(name))

    def constraint(self, name: str, index: list[str]) -> str | None:
        return self._render(name, index, self._constraint_sets.get(name))

    def _render(self, name: str, index: list[str], index_sets: list[str] | None) -> str | None:
        """Return the labelled key, or None when this key has nothing to label.

        The test is whether any set this key is indexed by declares labels — not
        whether these particular elements happen to appear in the map. A partial
        map would otherwise render some rows of one column as
        ``place[db_y2, hall_a, Sun 08:30]`` and others as the raw
        ``place[db_y2,hall_a,7]``; an unmapped element simply renders as itself.

        Returning None rather than a copy of the key lets every caller write
        ``label or key``, and lets the API omit the field for models that have
        no labels at all.
        """
        if not self.active or not index or not index_sets:
            return None
        if len(index_sets) != len(index):
            # A mismatch means the model and the key disagree; rendering a
            # misaligned label would be worse than rendering none.
            return None
        if not any(set_name in self._labels for set_name in index_sets):
            return None
        parts = [
            self.element(set_name, element)
            for set_name, element in zip(index_sets, index, strict=True)
        ]
        return f"{name}[{', '.join(parts)}]"

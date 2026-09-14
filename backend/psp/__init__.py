"""PSP — a generic Problem-Solving Platform.

Layering (strict, one direction only):

    domain  ->  problem  ->  ir  ->  compiler  ->  solvers
                   \\                                 /
                    `------ provenance <------------'

Nothing below ``ir`` may import a solver package, and no solver package may
import ``problem`` or ``domain``.  That is what makes the platform
solver-independent: replacing a solver never touches the business model.
"""

__version__ = "0.1.0"

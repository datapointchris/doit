"""doit — the layer that decides what to attend to and drives it.

Scheduling comes in two halves and doit uses both. :mod:`doit.cadence` is the
deterministic one: a declared interval, a derived due date, an item that is due
or it isn't — what review and labs run on. :mod:`doit.allocate` is the weighted
one: a stated share of attention, an interval implied by that share, and a draw
rather than a ranking — what the draw runs on. A pursuit can use both, and does:
an explicit cadence pins it when overdue instead of leaving it to chance.
"""

__version__ = '0.1.0'

"""doit's rules, which the service in api/ runs to answer every view.

Scheduling comes in two halves and doit uses both. :mod:`doit.cadence` is the
deterministic one: a declared interval, a derived due date, an item that is due
or it isn't — what review runs on. :mod:`doit.allocate` is what
`doit next` runs on: a running balance against a declared pace, and a weight
that orders what is owed and draws what fills the rest of the screen.
"""

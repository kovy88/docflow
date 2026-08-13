"""Pure domain layer.

Nothing in this package performs I/O. No database sessions, no HTTP clients, no file
handles. That constraint is what makes the business rules testable in milliseconds
without any infrastructure, and it is enforced by review rather than by tooling:
if you find yourself wanting to import SQLAlchemy here, the logic belongs in
`docflow.services` instead.
"""

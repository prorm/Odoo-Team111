"""Shared column-type helpers for every model module.

`StrEnum` lived inline in the old single-file `entities.py`; it moved here when
the models were split per-domain (Architecture §3) so `user.py`,
`employee.py`, `payroll.py` etc. can all reach it without importing each other.
"""
from sqlalchemy import Enum as SQLEnum


def StrEnum(enum_cls, **kwargs):
    """A Python enum stored as its `.value` string, not a native Postgres enum.

    `native_enum=False` keeps enum evolution a plain data migration
    (`UPDATE ... SET role = ...`) instead of `ALTER TYPE`, which matters here
    because the role vocabulary is expected to change with the product (it did
    exactly that when the generic asset roles became PeoplePay360's five HR
    roles). It also lets partial-index / EXCLUDE predicates compare against a
    string literal directly, with no cast — see the Contract non-overlap
    constraint's `WHERE (status = 'active')`.
    """
    return SQLEnum(enum_cls, native_enum=False, values_callable=lambda x: [e.value for e in x], **kwargs)

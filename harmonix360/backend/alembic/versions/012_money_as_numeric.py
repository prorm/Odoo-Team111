"""012_money_as_numeric

Revision ID: 012_money_as_numeric
Revises: 011_drop_decorative_version
Create Date: 2026-08-10 11:00:00.000000

Converts the one monetary column in Harmonix360's schema from binary floating
point to fixed-point decimal.

    assets.purchase_cost : double precision -> numeric(12, 2)

Why
---
`double precision` cannot represent most decimal fractions exactly: 0.10 stored
as a float is really 0.1000000000000000055511151231257827..., so ten of them sum
to 0.9999999999999999 rather than 1.00. For an asset register that feeds a
"high-value asset" threshold (app/jobs/tasks/transfer_decision.py compares
purchase cost against $5000) and would feed any future depreciation or
book-value total, that drift is silent and compounding. `numeric(12, 2)` stores
the decimal value itself, and asyncpg maps it to Python `decimal.Decimal`, so
the exactness survives all the way into application code.

Scope of the audit behind this migration
----------------------------------------
Every `Float` column in app/models/entities.py was reviewed, not just the
obvious one. `assets.purchase_cost` was the only monetary field in the schema —
it is the sole `Float` column in the entire model layer. Nothing else in
Harmonix360's AssetFlow domain stores a price, amount, fee, fare, budget or
expense, so nothing else is converted here. The rule that keeps this from
regressing in a future domain is written into HARMONIX360_ARCHITECTURE.md
Section 2, rule 9.

12 digits total / 2 decimal places allows values up to 9,999,999,999.99, which
is far above any plausible single asset purchase price while staying well
within a form a human reads without counting digits.

Data impact
-----------
Existing values are CONVERTED IN PLACE, not dropped:
`USING purchase_cost::numeric(12, 2)`.

Two consequences worth stating plainly:

  1. Values with more than 2 decimal places are ROUNDED (half-up) to 2. A cost
     stored as 1899.999 becomes 1900.00. This is intended — the column now
     declares that Harmonix360 tracks purchase cost to the cent — but it is a real
     transformation, not a no-op re-typing.
  2. A value whose integer part exceeds 10 digits would overflow numeric(12, 2)
     and abort the migration. That fails loudly rather than silently truncating.

On the development database `assets` currently holds 0 rows, so neither case
arises there; both are documented for any environment that does hold data.

DOWNGRADE converts back with `USING purchase_cost::double precision`. Values
round-trip numerically, but the exactness guarantee is lost the moment the
column is a float again.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '012_money_as_numeric'
down_revision: Union[str, None] = '011_drop_decorative_version'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'assets',
        'purchase_cost',
        existing_type=sa.Float(),
        type_=sa.Numeric(precision=12, scale=2),
        existing_nullable=True,
        postgresql_using='purchase_cost::numeric(12, 2)',
    )


def downgrade() -> None:
    op.alter_column(
        'assets',
        'purchase_cost',
        existing_type=sa.Numeric(precision=12, scale=2),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using='purchase_cost::double precision',
    )

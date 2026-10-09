from __future__ import annotations

from app.database.models.reconciliation_run import ReconciliationRunORM
from app.database.repositories.base import BaseRepository


class ReconciliationRunRepository(BaseRepository[ReconciliationRunORM]):
    model = ReconciliationRunORM

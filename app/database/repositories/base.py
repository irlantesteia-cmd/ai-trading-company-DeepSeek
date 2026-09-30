from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base


class BaseRepository[T: Base]:
    """Repositório base com operações CRUD mínimas.

    Usa a sintaxe PEP 695 (Python 3.12+) para parâmetros de tipo.
    """

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, obj: T) -> T:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def get(self, pk: int) -> T | None:
        return await self.session.get(self.model, pk)

    async def list_all(self, limit: int = 100) -> list[T]:
        result = await self.session.execute(select(self.model).limit(limit))
        return list(result.scalars().all())
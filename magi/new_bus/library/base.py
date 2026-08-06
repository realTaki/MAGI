"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / dto_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Generic, TypeVar

from magi.new_bus.db.base import Base
from magi.new_bus.db.engine import EngineFactory

RowT = TypeVar("RowT", bound=Base)
DtoT = TypeVar("DtoT")


class BaseBook(Generic[RowT, DtoT]):
    """子类设置 model_cls / dto_cls，自动处理 Session 和映射。"""

    model_cls: type[RowT]
    dto_cls: type[DtoT]

    def __init__(self, factory: EngineFactory):
        self._factory = factory

    def _session(self):
        return self._factory.session()

    def _row_to_dto(self, row: RowT) -> DtoT:
        kwargs: dict = {}
        for f in dataclasses.fields(self.dto_cls):
            if hasattr(row, f.name):
                val = getattr(row, f.name)
                if isinstance(val, datetime):
                    kwargs[f.name] = val.isoformat()
                else:
                    kwargs[f.name] = val
        return self.dto_cls(**kwargs)

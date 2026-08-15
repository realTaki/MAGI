"""BaseBook — 数据簿基类，自动映射 ORM → dataclass。

子类提供 model_cls / dto_cls 两个类属性即可。
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import ClassVar, Protocol, TypeVar, overload

from magi.bus.db.base import Base
from magi.bus.db.engine import EngineFactory

RowT = TypeVar("RowT", bound=Base)


class _Dataclass(Protocol):
    """Structural Protocol for ``@dataclasses.dataclass``-decorated classes.

    ``dataclasses.fields()`` types its argument as
    ``DataclassInstance | type[DataclassInstance]``; without this
    bound, Pylance sees ``type[DtoT]`` (a TypeVar with no upper
    bound) as ``type[Any]`` and rejects the call. Binding DtoT to
    this Protocol makes the class satisfy the dataclass shape
    structurally without forcing every subclass to inherit from a
    concrete base.
    """

    __dataclass_fields__: ClassVar[dict]


DtoT = TypeVar("DtoT", bound=_Dataclass)


# ``to_iso`` is called from two very different contexts:
#
# - ``_row_to_dto`` passes a ``datetime`` and wants back ``str`` so it can
#   write the field directly onto the DTO dataclass.
# - ``TaskOut``/``TaskRunOut`` (the API wire form) wants the same string,
#   but Pylance can't narrow ``datetime | str | None -> str | None`` down
#   to ``str`` at the call site, so the build-time signature reports
#   ``reportArgumentType`` against the ``str`` field. The overloads below
#   let the type checker infer the exact return shape per input shape
#   without forcing every caller to add ``# type: ignore`` or ``cast``.
@overload
def to_iso(value: datetime) -> str: ...
@overload
def to_iso(value: str) -> str: ...
@overload
def to_iso(value: None) -> None: ...
def to_iso(value: datetime | str | None) -> str | None:
    """ISO-8601 UTC string with an explicit trailing ``Z``.

    Every ``DateTime`` column in bus stores **naive UTC**
    (``utcnow_naive`` is the column default everywhere). Emitting
    those through a bare ``datetime.isoformat()`` produces a string
    with no timezone marker, which every JSON consumer — the WebUI,
    the LLM tool layer, ``new Date(...)`` in the browser — is
    entitled to read as *local* time. The ``Z`` makes the UTC
    contract explicit on the wire.

    Aware datetimes are converted to UTC first, so the output shape
    is identical regardless of which path produced the value.

    Pass-through for ``str`` / ``None``: callers that hand back an
    already-serialised value (or a DTO built by hand rather than by
    :meth:`BaseBook._row_to_dto`) don't have to know which path
    they're on.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class BaseBook[RowT: Base, DtoT: _Dataclass]:
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
                    kwargs[f.name] = to_iso(val)
                else:
                    kwargs[f.name] = val
        return self.dto_cls(**kwargs)

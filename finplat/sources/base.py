"""Interface chung cho mọi nguồn dữ liệu.

Một nguồn = một module trong finplat/sources/, khai báo một lớp kế thừa `Source` và đăng ký
bằng `@register`. Nguồn cần cài đặt 2 hàm:

    fetch(dataset, **params) -> list[dict]   # raw đúng như nguồn trả về (JSON được)
    parse(dataset, raw, params) -> list[...]  # chuyển raw sang record chung trong schemas.py

Không có gì khác trong hệ thống phải sửa khi thêm nguồn.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

_REGISTRY: dict[str, "Source"] = {}
_FACTORIES: list[Callable[[], list["Source"]]] = []


class Source(ABC):
    name: str = ""
    datasets: tuple[str, ...] = ()

    @abstractmethod
    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        ...

    @abstractmethod
    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list:
        ...

    def supports(self, dataset: str) -> bool:
        return dataset in self.datasets

    def __repr__(self) -> str:
        return f"<Source {self.name}>"


def register(source: "Source") -> "Source":
    _REGISTRY[source.name] = source
    return source


def register_factory(factory: Callable[[], list["Source"]]) -> Callable[[], list["Source"]]:
    """Cho các nguồn sinh từ cấu hình (vd mỗi feed RSS một nguồn)."""
    _FACTORIES.append(factory)
    return factory


def _ensure_loaded() -> None:
    from . import cafef, demo, disclosures, rss, vndirect, vnstock_source  # noqa: F401  (đăng ký khi import)

    while _FACTORIES:
        for src in _FACTORIES.pop()():
            register(src)


def get_source(name: str) -> Source:
    _ensure_loaded()
    if name not in _REGISTRY:
        raise KeyError(f"Không có nguồn '{name}'. Các nguồn: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def all_sources() -> dict[str, Source]:
    _ensure_loaded()
    return dict(_REGISTRY)

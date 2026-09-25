"""Estructuras de datos del dominio."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Producto:
    id: int
    marca: str
    detalle: str

    @property
    def nombre(self) -> str:
        return f"{self.marca} {self.detalle}"


@dataclass(frozen=True)
class RegistroPrecio:
    """Un precio observado de un producto en un distribuidor en una fecha."""

    producto_id: int
    distribuidor: str
    precio: float
    fecha: date
    fuente: str = "manual"

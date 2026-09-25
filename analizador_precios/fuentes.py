"""Fuentes externas de precios de distribuidores.

Cada fuente devuelve los precios que encuentra hoy para un producto. El
analizador los guarda en la base de datos, de modo que cada consulta además
alimenta el historial.

Para agregar un distribuidor nuevo basta con implementar `FuentePrecios`
(por ejemplo, leyendo la API o el catálogo de la tienda) y pasarlo al
`AnalizadorPrecios`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .texto import similitud


@dataclass(frozen=True)
class Oferta:
    distribuidor: str
    titulo: str
    precio: float
    url: str = ""


class FuentePrecios(Protocol):
    nombre: str

    def buscar(self, marca: str, detalle: str) -> list[Oferta]: ...


class MercadoLibre:
    """Consulta la API pública de búsqueda de Mercado Libre.

    Mercado Libre exige un token de acceso para su API de búsqueda; se lee de
    la variable de entorno ``ML_ACCESS_TOKEN``. ``sitio`` es el código de país
    (MLA Argentina, MLC Chile, MLM México, MCO Colombia, MPE Perú, MLU Uruguay…).
    Cada vendedor se trata como un distribuidor distinto.
    """

    nombre = "mercadolibre"
    URL = "https://api.mercadolibre.com/sites/{sitio}/search"

    def __init__(
        self,
        sitio: str = "MLC",
        token: str | None = None,
        limite: int = 20,
        similitud_minima: float = 0.6,
        timeout: float = 10.0,
    ):
        self.sitio = sitio
        self.token = token or os.environ.get("ML_ACCESS_TOKEN")
        self.limite = limite
        self.similitud_minima = similitud_minima
        self.timeout = timeout

    def _pedir(self, consulta: str) -> dict:
        params = urllib.parse.urlencode({"q": consulta, "limit": self.limite, "condition": "new"})
        req = urllib.request.Request(f"{self.URL.format(sitio=self.sitio)}?{params}")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def buscar(self, marca: str, detalle: str) -> list[Oferta]:
        consulta = f"{marca} {detalle}"
        datos = self._pedir(consulta)
        ofertas = []
        for item in datos.get("results", []):
            titulo = item.get("title", "")
            precio = item.get("price")
            # Descarta accesorios y modelos distintos que la búsqueda devuelve.
            if not precio or similitud(consulta, titulo) < self.similitud_minima:
                continue
            vendedor = (item.get("seller") or {}).get("nickname") or str(
                (item.get("seller") or {}).get("id", "desconocido")
            )
            ofertas.append(
                Oferta(f"MercadoLibre - {vendedor}", titulo, float(precio), item.get("permalink", ""))
            )
        return ofertas

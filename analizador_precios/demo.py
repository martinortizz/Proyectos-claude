"""Datos de ejemplo FICTICIOS para probar el sistema sin cargar precios reales."""

from __future__ import annotations

import random
from datetime import date, timedelta

from .db import BaseDatos

# (marca, detalle, precio base, distribuidores y su recargo habitual sobre el precio base)
PRODUCTOS = [
    ("Samsung", "Galaxy S24 128GB Negro", 899_990,
     {"Tienda Uno": 0.00, "ElectroMax": 0.04, "MegaStore": -0.03, "TecnoShop": 0.08}),
    ("Apple", "iPhone 15 128GB", 1_049_990,
     {"Tienda Uno": 0.02, "ElectroMax": 0.00, "iStore Centro": 0.05, "MegaStore": -0.02}),
    ("LG", "Smart TV 55 pulgadas 4K UHD 55UR8750", 429_990,
     {"Tienda Uno": 0.00, "ElectroMax": 0.06, "MegaStore": -0.05, "HogarPlus": 0.03}),
    ("Sony", "Audífonos WH-1000XM5", 349_990,
     {"TecnoShop": 0.00, "ElectroMax": 0.05, "AudioPro": -0.04}),
    ("Nike", "Zapatillas Air Max 90 Hombre", 129_990,
     {"DeporteTotal": 0.00, "Tienda Uno": 0.07, "SportCenter": -0.05}),
]


def cargar_demo(db: BaseDatos, hoy: date | None = None, meses: int = 12, semilla: int = 7) -> int:
    """Genera un año de precios semanales con una leve baja y ofertas ocasionales."""
    hoy = hoy or date.today()
    rnd = random.Random(semilla)
    n = 0
    for marca, detalle, base, distribuidores in PRODUCTOS:
        producto = db.obtener_o_crear_producto(marca, detalle)
        semanas = meses * 52 // 12
        for semana in range(semanas, -1, -1):
            fecha = hoy - timedelta(weeks=semana)
            # El precio baja ~10 % a lo largo del período.
            tendencia = 1 + 0.10 * semana / semanas
            for distribuidor, recargo in distribuidores.items():
                oferta = 0.85 if rnd.random() < 0.06 else 1.0
                ruido = rnd.uniform(-0.02, 0.02)
                precio = base * tendencia * (1 + recargo + ruido) * oferta
                db.agregar_precio(producto, distribuidor, round(precio, -1), fecha, "demo")
                n += 1
    return n

"""Analizador de precios de productos.

Compara el precio ingresado por el usuario con los precios actuales de los
distintos distribuidores y con el historial de precios del producto, y lo
clasifica como: muy barato, barato, precio promedio o más caro que el promedio.
"""

from .analisis import AnalizadorPrecios, Clasificacion, ResultadoAnalisis, Umbrales
from .db import BaseDatos

__all__ = [
    "AnalizadorPrecios",
    "BaseDatos",
    "Clasificacion",
    "ResultadoAnalisis",
    "Umbrales",
]

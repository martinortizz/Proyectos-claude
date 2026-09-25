"""Normalización de texto y búsqueda aproximada de productos."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


UNIDADES = "gb|tb|mb|mah|w|hz|mm|cm|m|kg|kilos?|g|gr|grs|gramos|ml|cc|l|lt|lts|litros?|mp|pulg|pulgadas|in|un|unidades"
# Formas equivalentes de la misma unidad, llevadas a una sola.
SINONIMOS_UNIDAD = {
    "kilo": "kg", "kilos": "kg", "gr": "g", "grs": "g", "gramos": "g", "cc": "ml",
    "lt": "l", "lts": "l", "litro": "l", "litros": "l", "pulgadas": "pulg", "unidades": "un",
}


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y sin signos de puntuación."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto.lower())
    # "128 gb" -> "128gb", "65 w" -> "65w" para que coincidan ambas formas.
    texto = re.sub(r"\b(\d+) ?(" + UNIDADES + r")\b",
                   lambda m: m.group(1) + SINONIMOS_UNIDAD.get(m.group(2), m.group(2)), texto)
    return " ".join(texto.split())


def leer_precio(valor: str | float | int) -> float:
    """Convierte textos como "$ 1.299,90", "1,299.90" o "1299" en número.

    Si aparecen punto y coma, el último es el separador decimal. Si aparece
    uno solo seguido de exactamente tres dígitos ("1.299"), se toma como
    separador de miles, que es lo habitual al escribir precios en español.
    """
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = re.sub(r"[^\d.,-]", "", valor)
    if not re.search(r"\d", txt):
        raise ValueError(f"Precio inválido: {valor!r}")
    if "." in txt and "," in txt:
        decimal = "." if txt.rfind(".") > txt.rfind(",") else ","
        miles = "," if decimal == "." else "."
        txt = txt.replace(miles, "").replace(decimal, ".")
    else:
        sep = "." if "." in txt else "," if "," in txt else None
        if sep:
            partes = txt.split(sep)
            if len(partes) > 2 or len(partes[-1]) == 3:
                txt = "".join(partes)
            else:
                txt = ".".join(partes)
    try:
        return float(txt)
    except ValueError:
        raise ValueError(f"Precio inválido: {valor!r}") from None


def similitud(consulta: str, candidato: str) -> float:
    """Puntaje entre 0 y 1 que combina coincidencia de palabras y de caracteres.

    Las palabras de la consulta que aparecen en el candidato pesan más que
    el orden, de modo que "Galaxy S24 128GB" encuentra
    "Galaxy S24 5G 128 GB negro".
    """
    a, b = normalizar(consulta), normalizar(candidato)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    cobertura = len(ta & tb) / len(ta)
    # "128gb" vs "128 gb": comparar también sin espacios.
    caracteres = SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio()
    return 0.6 * cobertura + 0.4 * caracteres


def formato_dinero(x: float) -> str:
    """1234567.5 -> "$1.234.567,50"; omite los decimales si son cero."""
    txt = f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return "$" + (txt[:-3] if txt.endswith(",00") else txt)

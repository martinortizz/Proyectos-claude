"""Búsqueda de precios en tiendas chilenas: Paris, Falabella, Ripley, Líder, Jumbo y Mercado Libre.

Cada tienda publica sus resultados de búsqueda de forma distinta, pero casi
todas incrustan los datos de los productos como JSON dentro del HTML
(``__NEXT_DATA__``, ``window.__PRELOADED_STATE__``, JSON-LD de schema.org…)
o los sirven desde una API JSON. Por eso cada tienda define dónde buscar y
el extractor genérico recorre ese JSON buscando objetos con nombre y precio.

Se registra el precio que paga cualquier cliente por internet (precio
oferta o normal). Los precios exclusivos con tarjeta de la tienda (CMR,
Tarjeta Ripley, Cencosud, etc.) se ignoran.

Los sitios cambian su estructura sin aviso: si una tienda deja de devolver
precios, el resumen diario lo informa como error para poder ajustarla aquí.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from html import unescape
from typing import Callable, Iterator

from .fuentes import Oferta
from .texto import leer_precio, normalizar, similitud

AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Claves que traen el nombre del producto, su marca, su URL y su precio.
CLAVES_NOMBRE = ("displayName", "productName", "name", "title", "nombre")
CLAVES_MARCA = ("brand", "brandName", "manufacturer", "marca")
CLAVES_URL = ("url", "link", "permalink", "productUrl", "href", "linkText")
CLAVES_PRECIO = (
    "offerPrice", "salePrice", "internetPrice", "bestPrice", "sellingPrice", "Price",
    "price", "lowPrice", "amount", "listPrice", "normalPrice", "precio",
)
# Precios que no paga cualquier cliente.
PALABRAS_TARJETA = ("card", "cmr", "tarjeta", "ripley", "cencosud", "puntos", "points", "installment", "cuota")
# Campos con "price" en el nombre que no son el precio del producto.
PALABRAS_NO_PRECIO = ("unit", "ppum", "kilo", "shipping", "envio", "discount", "descuento", "saving", "ahorro", "tax")


class ErrorTienda(Exception):
    """La tienda no respondió o respondió algo que no se pudo leer."""


def descargar(url: str, timeout: float = 25.0, encabezados: dict | None = None, reintentos: int = 2) -> str:
    cab = {
        "User-Agent": AGENTE,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CL,es;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }
    cab.update(encabezados or {})
    ultimo_error: Exception | None = None
    for intento in range(reintentos + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=timeout) as r:
                datos = r.read()
                codif = r.headers.get("Content-Encoding", "")
                if codif == "gzip":
                    datos = gzip.decompress(datos)
                elif codif == "deflate":
                    datos = zlib.decompress(datos)
                return datos.decode(r.headers.get_content_charset() or "utf-8", "replace")
        except Exception as e:  # red, HTTP 403/5xx, etc.
            ultimo_error = e
            if intento < reintentos:
                time.sleep(2 * (intento + 1))
    raise ErrorTienda(f"{type(ultimo_error).__name__}: {ultimo_error}")


# ─────────────────────────────── extracción genérica ───────────────────────────────

_RE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_RE_ASIGNACION = re.compile(r"^\s*(?:window\.)?[\w$.]+\s*=\s*", re.S)


def bloques_json(html: str) -> Iterator[object]:
    """Todo el JSON incrustado en <script>: application/json, ld+json y `window.X = {...}`."""
    for atributos, cuerpo in _RE_SCRIPT.findall(html):
        cuerpo = cuerpo.strip()
        if not cuerpo:
            continue
        if "json" in atributos.lower():
            candidatos = [cuerpo]
        else:
            # window.__PRELOADED_STATE__ = {...};  /  var x = {...}
            sin_var = re.sub(r"^\s*(?:var|let|const)\s+", "", cuerpo)
            if not _RE_ASIGNACION.match(sin_var):
                continue
            candidatos = [_RE_ASIGNACION.sub("", sin_var, count=1).rstrip().rstrip(";")]
        for c in candidatos:
            try:
                yield json.loads(unescape(c) if c.startswith("&") else c)
            except ValueError:
                continue


def _texto(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("name", "value", "label"):
            if isinstance(v.get(k), str):
                return v[k]
    if isinstance(v, list) and v and isinstance(v[0], str):
        return v[0]
    return ""


def _es_tarjeta(etiqueta: str) -> bool:
    e = etiqueta.lower()
    return any(p in e for p in PALABRAS_TARJETA)


def _a_numero(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, list) and v:
        return _a_numero(v[0])
    if isinstance(v, str) and re.search(r"\d", v) and len(v) < 30:
        try:
            n = leer_precio(v)
            return n if n > 0 else None
        except ValueError:
            return None
    return None


def precios_de(obj: dict) -> list[float]:
    """Precios al público de un objeto producto (sin precios con tarjeta)."""
    encontrados: list[float] = []

    def mirar(o: dict, profundidad: int) -> None:
        if profundidad > 4:
            return
        for k, v in o.items():
            if _es_tarjeta(k) or any(p in k.lower() for p in PALABRAS_NO_PRECIO):
                continue
            if k in CLAVES_PRECIO or k.lower().endswith("price"):
                n = _a_numero(v)
                if n:
                    encontrados.append(n)
                elif isinstance(v, dict):
                    mirar(v, profundidad + 1)
            elif k in ("prices", "offers", "priceRange", "pricing", "commertialOffer", "sellers", "items", "precios"):
                for x in v if isinstance(v, list) else [v]:
                    if not isinstance(x, dict):
                        continue
                    # Falabella: {"type": "cmrPrice", "price": ["999.990"]}
                    tipo = str(x.get("type") or x.get("label") or x.get("name") or "")
                    if _es_tarjeta(tipo):
                        continue
                    mirar(x, profundidad + 1)

    mirar(obj, 0)
    return encontrados


def productos_en(datos: object, limite: int = 400) -> list[dict]:
    """Recorre JSON arbitrario y devuelve objetos que parecen productos con precio."""
    salida: list[dict] = []
    vistos: set[tuple[str, float]] = set()
    pila = [datos]
    while pila and len(salida) < limite:
        o = pila.pop()
        if isinstance(o, list):
            pila.extend(reversed(o))
            continue
        if not isinstance(o, dict):
            continue
        nombre = next((_texto(o[k]) for k in CLAVES_NOMBRE if k in o and _texto(o[k])), "")
        precios = precios_de(o) if nombre else []
        if nombre and precios and len(nombre) > 3:
            precio = min(precios)
            clave = (nombre, precio)
            if clave not in vistos:
                vistos.add(clave)
                salida.append({
                    "titulo": nombre.strip(),
                    "marca": next((_texto(o[k]) for k in CLAVES_MARCA if k in o and _texto(o[k])), ""),
                    "precio": precio,
                    "url": next((o[k] for k in CLAVES_URL if isinstance(o.get(k), str)), ""),
                })
            continue  # no bajar dentro de un producto ya reconocido
        pila.extend(reversed(list(o.values())))
    return salida


# ─────────────────────────────────── tiendas ───────────────────────────────────

@dataclass
class Tienda:
    nombre: str
    base: str                                   # para completar URLs relativas
    urls: Callable[[str], list[str]]            # URLs a probar, en orden, para una búsqueda
    encabezados: dict | None = None

    def buscar_crudo(self, consulta: str) -> list[dict]:
        errores = []
        for url in self.urls(consulta):
            try:
                texto = descargar(url, encabezados=self.encabezados)
            except ErrorTienda as e:
                errores.append(f"{url}: {e}")
                continue
            texto_l = texto.lstrip()
            bloques = [json.loads(texto_l)] if texto_l[:1] in "[{" else list(bloques_json(texto))
            productos = [p for b in bloques for p in productos_en(b)]
            if productos:
                for p in productos:
                    if p["url"] and not p["url"].startswith("http"):
                        p["url"] = urllib.parse.urljoin(self.base, p["url"])
                return productos
            errores.append(f"{url}: la página no trajo productos reconocibles")
        raise ErrorTienda("; ".join(errores) or "sin URLs")


def _q(texto: str) -> str:
    return urllib.parse.quote(texto)


def _q_plus(texto: str) -> str:
    return urllib.parse.quote_plus(texto)


def _mercadolibre_urls(consulta: str) -> list[str]:
    urls = []
    if os.environ.get("ML_ACCESS_TOKEN"):
        urls.append(f"https://api.mercadolibre.com/sites/MLC/search?q={_q_plus(consulta)}&condition=new&limit=30")
    slug = re.sub(r"[^a-z0-9]+", "-", normalizar(consulta)).strip("-")
    urls.append(f"https://listado.mercadolibre.cl/{slug}_ITEM*CONDITION_2230284_NoIndex_True")
    return urls


TIENDAS: dict[str, Tienda] = {
    "Falabella": Tienda(
        "Falabella", "https://www.falabella.com",
        lambda q: [
            f"https://www.falabella.com/s/browse/v1/search/cl?page=1&Ntt={_q_plus(q)}",
            f"https://www.falabella.com/falabella-cl/search?Ntt={_q_plus(q)}",
        ],
    ),
    "Paris": Tienda(
        "Paris", "https://www.paris.cl",
        lambda q: [f"https://www.paris.cl/search/?q={_q_plus(q)}", f"https://www.paris.cl/search?q={_q_plus(q)}"],
    ),
    "Ripley": Tienda(
        "Ripley", "https://simple.ripley.cl",
        lambda q: [f"https://simple.ripley.cl/search/{_q(q)}"],
    ),
    "Líder": Tienda(
        "Líder", "https://super.lider.cl",
        lambda q: [f"https://super.lider.cl/search?q={_q_plus(q)}"],
    ),
    "Jumbo": Tienda(
        "Jumbo", "https://www.jumbo.cl",
        lambda q: [
            f"https://www.jumbo.cl/api/catalog_system/pub/products/search/{_q(q)}?_from=0&_to=39",
            f"https://www.jumbo.cl/busqueda?ft={_q_plus(q)}",
        ],
    ),
    "Mercado Libre": Tienda(
        "Mercado Libre", "https://www.mercadolibre.cl",
        _mercadolibre_urls,
        {"Authorization": f"Bearer {os.environ['ML_ACCESS_TOKEN']}"} if os.environ.get("ML_ACCESS_TOKEN") else None,
    ),
}


# ─────────────────────────────── elegir la oferta ───────────────────────────────

# Palabras que delatan un accesorio que menciona al producto ("Funda iPhone 15").
ACCESORIOS = {
    "funda", "carcasa", "case", "lamina", "mica", "protector", "vidrio", "cargador", "cable",
    "adaptador", "soporte", "correa", "estuche", "repuesto", "control", "compatible", "para",
}
# Variantes de modelo: "iPhone 15" no es "iPhone 15 Pro".
VARIANTES = {"pro", "max", "plus", "mini", "ultra", "lite", "fe", "se", "air", "neo", "edge"}

def mejor_oferta(
    marca: str,
    detalle: str,
    productos: list[dict],
    tienda: str,
    referencia: float | None = None,
    minimo: float = 0.62,
) -> Oferta | None:
    """La oferta que corresponde al producto buscado.

    Se exige que la marca aparezca y que el título se parezca a marca +
    detalle. Entre las más parecidas (hasta 0,05 bajo la mejor) se toma la
    más barata. Con un precio de referencia se descartan precios muy
    distintos, que suelen ser accesorios (fundas, repuestos) o packs.
    """
    consulta = f"{marca} {detalle}"
    marca_n = normalizar(marca)
    palabras_consulta = set(normalizar(consulta).split())
    excluir = (ACCESORIOS | VARIANTES) - palabras_consulta
    # Números del modelo y la capacidad (15, 128gb, 55): todos deben aparecer.
    numeros = {t for t in palabras_consulta if any(c.isdigit() for c in t)}
    candidatos = []
    for p in productos:
        titulo = p["titulo"]
        texto = normalizar(f"{p.get('marca', '')} {titulo}")
        palabras = set(texto.split())
        if marca_n not in texto or excluir & palabras or not numeros <= palabras:
            continue
        puntaje = similitud(consulta, f"{p.get('marca', '')} {titulo}" if marca_n not in normalizar(titulo) else titulo)
        if puntaje < minimo:
            continue
        if referencia and not (0.4 * referencia <= p["precio"] <= 2.5 * referencia):
            continue
        candidatos.append((puntaje, p))
    if not candidatos:
        return None
    tope = max(s for s, _ in candidatos)
    elegido = min((p for s, p in candidatos if s >= tope - 0.05), key=lambda p: p["precio"])
    return Oferta(tienda, elegido["titulo"], round(elegido["precio"]), elegido.get("url", ""))

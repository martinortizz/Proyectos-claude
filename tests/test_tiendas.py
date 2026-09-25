import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from analizador_precios import recolector, tiendas
from analizador_precios.tiendas import Tienda, bloques_json, mejor_oferta, productos_en

HOY = date(2026, 9, 25)

# Estructuras reducidas del tipo que usan las tiendas (valores inventados).
API_FALABELLA = {"data": {"results": [
    {"displayName": "iPhone 15 128GB Negro", "brand": "APPLE", "url": "https://www.falabella.com/p/1",
     "prices": [{"type": "cmrPrice", "price": ["849.990"]},
                {"type": "internetPrice", "price": ["899.990"]},
                {"type": "normalPrice", "price": ["999.990"], "crossed": True}]},
    {"displayName": "Funda iPhone 15", "brand": "APPLE", "prices": [{"type": "internetPrice", "price": ["19.990"]}]},
]}}

HTML_RIPLEY = """<html><script>window.__PRELOADED_STATE__ = {"products": [
  {"name": "Apple iPhone 15 128GB", "manufacturer": "APPLE", "url": "/apple-iphone-15-2000",
   "prices": {"listPrice": 999990, "offerPrice": 889990, "cardPrice": 829990}}
]};</script></html>"""

HTML_JSONLD = """<script type="application/ld+json">{"@type": "ItemList", "itemListElement": [
  {"@type": "Product", "name": "Leche Entera Soprole 1 L", "brand": {"name": "Soprole"},
   "offers": {"@type": "Offer", "price": "1190", "priceCurrency": "CLP"}}]}</script>"""

HTML_NEXT = """<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"search":
  {"items": [{"title": "Leche Entera Soprole 1 L", "brandName": "Soprole",
              "price": {"price": 1150, "unitPrice": 1150, "pricePerUnit": "1.150"},
              "unitPrice": 5}]}}}}</script>"""


class TestExtraccion(unittest.TestCase):
    def test_api_json_ignora_precio_tarjeta(self):
        ps = productos_en(API_FALABELLA)
        iphone = next(p for p in ps if p["titulo"].startswith("iPhone"))
        self.assertEqual(iphone["precio"], 899990)
        self.assertEqual(iphone["marca"], "APPLE")

    def test_estado_precargado(self):
        ps = [p for b in bloques_json(HTML_RIPLEY) for p in productos_en(b)]
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0]["precio"], 889990)

    def test_json_ld(self):
        ps = [p for b in bloques_json(HTML_JSONLD) for p in productos_en(b)]
        self.assertEqual((ps[0]["precio"], ps[0]["marca"]), (1190, "Soprole"))

    def test_next_data_ignora_precio_unitario(self):
        ps = [p for b in bloques_json(HTML_NEXT) for p in productos_en(b)]
        self.assertEqual(ps[0]["precio"], 1150)


class TestMejorOferta(unittest.TestCase):
    PRODUCTOS = [
        {"titulo": "iPhone 15 128GB Negro", "marca": "APPLE", "precio": 899990, "url": "a"},
        {"titulo": "iPhone 15 128GB Azul", "marca": "APPLE", "precio": 879990, "url": "b"},
        {"titulo": "iPhone 15 Pro Max 256GB", "marca": "APPLE", "precio": 1399990, "url": "c"},
        {"titulo": "Funda silicona iPhone 15 128GB", "marca": "APPLE", "precio": 19990, "url": "d"},
        {"titulo": "Galaxy S24 128GB", "marca": "SAMSUNG", "precio": 700000, "url": "e"},
    ]

    def test_elige_la_mas_barata_entre_las_que_coinciden(self):
        o = mejor_oferta("Apple", "iPhone 15 128GB", self.PRODUCTOS, "Falabella", referencia=900000)
        self.assertEqual((o.precio, o.url), (879990, "b"))

    def test_sin_referencia_descarta_por_parecido(self):
        o = mejor_oferta("Apple", "iPhone 15 128GB", self.PRODUCTOS[2:], "Falabella")
        self.assertIsNone(o)

    def test_exige_la_marca(self):
        self.assertIsNone(mejor_oferta("Xiaomi", "Galaxy S24 128GB", self.PRODUCTOS, "Paris"))


class TiendaFalsa(Tienda):
    def __init__(self, nombre, productos=None, error=None):
        super().__init__(nombre, "https://x", lambda q: [])
        self.productos, self.error = productos or [], error

    def buscar_crudo(self, consulta):
        if self.error:
            raise tiendas.ErrorTienda(self.error)
        return self.productos


class TestRecolector(unittest.TestCase):
    DOC = {"marca": "Apple", "detalle": "iPhone 15 128GB", "demo": False, "precios": [
        {"d": "Mi tienda", "p": 950000, "f": "2026-09-20", "o": "manual"},
        {"d": "Falabella", "p": 910000, "f": "2026-09-25", "o": "auto"},
    ]}

    def test_fusion_reemplaza_el_mismo_dia(self):
        doc = recolector.fusionar(self.DOC, [{"d": "Falabella", "p": 899990, "f": "2026-09-25", "o": "auto"}], HOY)
        fal = [r for r in doc["precios"] if r["d"] == "Falabella"]
        self.assertEqual([r["p"] for r in fal], [899990])
        self.assertEqual(len(doc["precios"]), 2)

    def test_fusion_respeta_tamano_maximo(self):
        muchos = [{"d": f"T{i % 6}", "p": 1000 + i, "f": "2026-01-01", "o": "auto", "t": "x" * 100} for i in range(3000)]
        doc = recolector.fusionar({**self.DOC, "precios": muchos}, [], HOY)
        self.assertLessEqual(len(json.dumps(doc).encode()), recolector.MAX_BYTES_DOC)

    def test_recolectar(self):
        docs = {
            "apple-iphone-15-128gb": self.DOC,
            "demo-x": {"marca": "Sony", "detalle": "X", "demo": True, "precios": []},
        }
        ts = {
            "Falabella": TiendaFalsa("Falabella", TestMejorOferta.PRODUCTOS),
            "Paris": TiendaFalsa("Paris", error="HTTPError 403"),
            "Jumbo": TiendaFalsa("Jumbo", []),
        }
        act, resumen = recolector.recolectar(docs, HOY, ts, pausa=0)
        self.assertEqual(list(act), ["apple-iphone-15-128gb"])  # el demo se omite
        self.assertEqual(resumen["tiendas"]["Falabella"]["encontrados"], 1)
        self.assertEqual(resumen["tiendas"]["Paris"]["errores"], 1)
        self.assertEqual(resumen["tiendas"]["Jumbo"]["sin_coincidencia"], 1)
        nuevo = next(r for r in act["apple-iphone-15-128gb"]["precios"] if r["d"] == "Falabella")
        self.assertEqual((nuevo["p"], nuevo["u"]), (879990, "b"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            cat = Path(d, "cat"); cat.mkdir()
            (cat / "apple-iphone-15-128gb.json").write_text(json.dumps(self.DOC), encoding="utf-8")
            falsas = {"Falabella": TiendaFalsa("Falabella", TestMejorOferta.PRODUCTOS)}
            with mock.patch.dict(recolector.TIENDAS, falsas, clear=True), \
                 mock.patch("sys.stdout"):
                recolector.main(["--catalogo", str(cat), "--salida", str(Path(d, "out")),
                                 "--fecha", "2026-09-25", "--pausa", "0"])
            self.assertTrue(Path(d, "out", "apple-iphone-15-128gb.json").exists())
            self.assertEqual(json.loads(Path(d, "out", "_resumen.json").read_text())["precios_nuevos"], 1)


if __name__ == "__main__":
    unittest.main()

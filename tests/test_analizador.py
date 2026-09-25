import io
import json
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from datetime import date, timedelta
from http.server import HTTPServer
from pathlib import Path

from analizador_precios import AnalizadorPrecios, BaseDatos, Clasificacion, Umbrales
from analizador_precios.cli import main
from analizador_precios.fuentes import Oferta
from analizador_precios.texto import formato_dinero, leer_precio, similitud
from analizador_precios.web import crear_manejador

HOY = date(2026, 9, 25)


def base_con_datos() -> tuple[BaseDatos, object]:
    """Producto con 4 distribuidores a 1000 hoy (promedio 1000) e historial plano de 1000."""
    db = BaseDatos(":memory:")
    p = db.obtener_o_crear_producto("Samsung", "Galaxy S24 128GB Negro")
    for semanas in range(1, 20):
        db.agregar_precio(p, "Histórico", 1000, HOY - timedelta(weeks=semanas + 5))
    for nombre, precio in [("A", 900), ("B", 1000), ("C", 1000), ("D", 1100)]:
        db.agregar_precio(p, nombre, precio, HOY - timedelta(days=2))
    return db, p


class TestTexto(unittest.TestCase):
    def test_leer_precio(self):
        casos = {
            "1299": 1299,
            "1.299": 1299,
            "$ 1.299.990": 1299990,
            "1.299,90": 1299.90,
            "1,299.90": 1299.90,
            "12,5": 12.5,
            "12.5": 12.5,
            "US$ 99": 99,
        }
        for texto, esperado in casos.items():
            self.assertAlmostEqual(leer_precio(texto), esperado, msg=texto)
        with self.assertRaises(ValueError):
            leer_precio("gratis")

    def test_formato_dinero(self):
        self.assertEqual(formato_dinero(1234567.5), "$1.234.567,50")
        self.assertEqual(formato_dinero(1000), "$1.000")

    def test_similitud_tolera_espacios_tildes_y_orden(self):
        self.assertGreater(similitud("galaxy s24 128 gb", "Galaxy S24 128GB Negro"), 0.7)
        self.assertGreater(similitud("audifonos sony", "Sony Audífonos WH-1000XM5"), 0.6)
        self.assertLess(similitud("iphone 15", "Galaxy S24 128GB"), 0.4)


class TestBusqueda(unittest.TestCase):
    def test_no_mezcla_marcas(self):
        db = BaseDatos(":memory:")
        db.obtener_o_crear_producto("LG", "Smart TV 55 pulgadas 4K")
        self.assertEqual(db.buscar_productos("Samsung", "Smart TV 55 pulgadas 4K"), [])
        self.assertEqual(len(db.buscar_productos("lg", "smart tv 55 4k")), 1)

    def test_producto_duplicado_se_reutiliza(self):
        db = BaseDatos(":memory:")
        a = db.obtener_o_crear_producto("Sony", "WH-1000XM5")
        b = db.obtener_o_crear_producto(" sony ", "wh 1000xm5")
        self.assertEqual(a.id, b.id)


class TestClasificacion(unittest.TestCase):
    def setUp(self):
        self.db, self.producto = base_con_datos()
        self.analizador = AnalizadorPrecios(self.db)

    def analizar(self, precio):
        return self.analizador.analizar("Samsung", "Galaxy S24 128GB", precio, hoy=HOY)

    def test_cuatro_categorias(self):
        esperados = {
            800: Clasificacion.MUY_BARATO,   # -20 %
            850: Clasificacion.MUY_BARATO,   # -15 % (límite incluido)
            900: Clasificacion.BARATO,       # -10 %
            950: Clasificacion.BARATO,       # -5 % (límite incluido)
            1000: Clasificacion.PROMEDIO,
            1050: Clasificacion.PROMEDIO,    # +5 % (límite incluido)
            1100: Clasificacion.CARO,
        }
        for precio, clase in esperados.items():
            self.assertEqual(self.analizar(precio).clasificacion, clase, msg=precio)

    def test_detalle_del_resultado(self):
        r = self.analizar(950)
        self.assertEqual(r.producto.id, self.producto.id)
        self.assertEqual([d.distribuidor for d in r.distribuidores][0], "A")
        self.assertEqual(r.promedio_distribuidores, 1000)
        self.assertEqual(r.historico.promedio, 1000)
        self.assertEqual(r.precio_referencia, 1000)
        self.assertEqual(r.diferencia_pct, -5)
        self.assertEqual(r.confianza, "alta")
        self.assertTrue(r.conclusiones)
        json.dumps(r.a_dict())  # serializable

    def test_referencia_combina_distribuidores_e_historico(self):
        db = BaseDatos(":memory:")
        p = db.obtener_o_crear_producto("Nike", "Air Max 90")
        db.agregar_precio(p, "Viejo", 1400, HOY - timedelta(days=200))
        db.agregar_precio(p, "Tienda", 1000, HOY)
        r = AnalizadorPrecios(db, peso_distribuidores=0.5).analizar("Nike", "Air Max 90", 1000, hoy=HOY)
        # distribuidores: 1000 · histórico: (1400 + 1000) / 2 = 1200 · referencia 1100
        self.assertEqual(r.precio_referencia, 1100)
        self.assertEqual(r.clasificacion, Clasificacion.BARATO)

    def test_precio_vencido_no_cuenta_como_actual(self):
        db = BaseDatos(":memory:")
        p = db.obtener_o_crear_producto("Nike", "Air Max 90")
        db.agregar_precio(p, "Tienda", 1000, HOY - timedelta(days=90))
        r = AnalizadorPrecios(db).analizar("Nike", "Air Max 90", 1000, hoy=HOY)
        self.assertEqual(r.distribuidores, [])
        self.assertEqual(r.precio_referencia, 1000)  # solo histórico
        self.assertEqual(r.confianza, "baja")

    def test_ultimo_precio_por_distribuidor(self):
        p = self.producto
        self.db.agregar_precio(p, "A", 700, HOY - timedelta(days=1))
        r = self.analizar(1000)
        a = next(d for d in r.distribuidores if d.distribuidor == "A")
        self.assertEqual(a.precio, 700)

    def test_sin_datos(self):
        r = self.analizador.analizar("Xiaomi", "Redmi Note 13", 100, hoy=HOY)
        self.assertIsNone(r.clasificacion)
        self.assertTrue(r.avisos)

    def test_umbrales_personalizados(self):
        a = AnalizadorPrecios(self.db, umbrales=Umbrales(-30, -20, 20))
        r = a.analizar("Samsung", "Galaxy S24 128GB", 850, hoy=HOY)
        self.assertEqual(r.clasificacion, Clasificacion.PROMEDIO)
        with self.assertRaises(ValueError):
            Umbrales(-5, -15, 5)

    def test_precio_invalido(self):
        with self.assertRaises(ValueError):
            self.analizar(0)


class FuenteFalsa:
    nombre = "falsa"

    def __init__(self, ofertas=None, error=None):
        self.ofertas, self.error = ofertas or [], error

    def buscar(self, marca, detalle):
        if self.error:
            raise self.error
        return self.ofertas


class TestFuentes(unittest.TestCase):
    def test_fuente_alimenta_historial_sin_duplicar(self):
        db, p = base_con_datos()
        fuente = FuenteFalsa([Oferta("Online", "Galaxy S24", 800)])
        a = AnalizadorPrecios(db, fuentes=[fuente])
        a.analizar("Samsung", "Galaxy S24 128GB Negro", 1000, hoy=HOY)
        r = a.analizar("Samsung", "Galaxy S24 128GB Negro", 1000, hoy=HOY)
        self.assertEqual(sum(1 for x in db.precios_de(p) if x.distribuidor == "Online"), 1)
        self.assertIn("Online", [d.distribuidor for d in r.distribuidores])

    def test_fuente_caida_no_rompe_el_analisis(self):
        db, _ = base_con_datos()
        a = AnalizadorPrecios(db, fuentes=[FuenteFalsa(error=OSError("sin red"))])
        r = a.analizar("Samsung", "Galaxy S24 128GB", 1000, hoy=HOY)
        self.assertEqual(r.clasificacion, Clasificacion.PROMEDIO)
        self.assertTrue(any("sin red" in x for x in r.avisos))


class TestImportacionYCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = str(self.dir / "p.db")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args) -> tuple[int, str]:
        salida = io.StringIO()
        with redirect_stdout(salida):
            codigo = main(["--db", self.db, *args])
        return codigo, salida.getvalue()

    def test_importar_y_analizar(self):
        hoy = date.today().isoformat()
        csv = self.dir / "precios.csv"
        csv.write_text(
            "marca,detalle,distribuidor,precio,fecha\n"
            f'Apple,iPhone 15 128GB,Tienda A,"1.000.000",{hoy}\n'
            f"Apple,iPhone 15 128GB,Tienda B,1100000,{hoy}\n",
            encoding="utf-8",
        )
        self.assertEqual(self.cli("importar", str(csv))[0], 0)
        codigo, out = self.cli("analizar", "Apple", "iPhone 15 128GB", "$ 850.000")
        self.assertEqual(codigo, 0)
        self.assertIn("MUY BARATO", out)

        codigo, out = self.cli("analizar", "Apple", "iPhone 15 128GB", "1050000", "--json")
        self.assertEqual(json.loads(out)["clasificacion"], "Precio promedio")

    def test_importar_fila_invalida(self):
        csv = self.dir / "malo.csv"
        csv.write_text("marca,detalle,distribuidor,precio\nApple,iPhone,Tienda,abc\n", encoding="utf-8")
        self.assertEqual(self.cli("importar", str(csv))[0], 1)

    def test_demo_y_registrar(self):
        self.cli("demo")
        codigo, _ = self.cli("analizar", "Sony", "WH-1000XM5", "300000", "--registrar", "Mi tienda")
        self.assertEqual(codigo, 0)
        _, out = self.cli("historial", "Sony", "WH-1000XM5")
        self.assertIn("Mi tienda", out)


class TestWeb(unittest.TestCase):
    def setUp(self):
        self.db, _ = base_con_datos()
        analizador = AnalizadorPrecios(self.db)
        # Fija "hoy" para que los datos de prueba sean vigentes.
        original = analizador.analizar
        analizador.analizar = lambda *a, **k: original(*a, hoy=HOY, **k)
        self.srv = HTTPServer(("127.0.0.1", 0), crear_manejador(self.db, analizador))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def post(self, ruta, datos):
        req = urllib.request.Request(
            self.base + ruta, json.dumps(datos).encode(), {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_pagina_y_analisis(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("Analizador de precios", r.read().decode())
        estado, r = self.post("/api/analizar", {"marca": "Samsung", "detalle": "Galaxy S24", "precio": "800"})
        self.assertEqual(estado, 200)
        self.assertEqual(r["clasificacion"], "Muy barato")

    def test_errores(self):
        estado, r = self.post("/api/analizar", {"marca": "Samsung", "precio": "800"})
        self.assertEqual(estado, 400)
        estado, _ = self.post("/api/precios", {"marca": "X", "detalle": "Y", "distribuidor": "Z", "precio": "-3"})
        self.assertEqual(estado, 400)


if __name__ == "__main__":
    unittest.main()

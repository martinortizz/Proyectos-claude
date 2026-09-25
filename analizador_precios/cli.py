"""Interfaz de línea de comandos.

Ejemplos:
    python -m analizador_precios demo
    python -m analizador_precios analizar "Samsung" "Galaxy S24 128GB" 799990
    python -m analizador_precios agregar "Samsung" "Galaxy S24 128GB" "Tienda Uno" 849990
    python -m analizador_precios importar precios.csv
    python -m analizador_precios web
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .analisis import AnalizadorPrecios, Clasificacion, ResultadoAnalisis, Umbrales
from .db import BaseDatos
from .demo import cargar_demo
from .fuentes import MercadoLibre
from .texto import formato_dinero, leer_precio

ICONOS = {
    Clasificacion.MUY_BARATO: "🟢🟢",
    Clasificacion.BARATO: "🟢",
    Clasificacion.PROMEDIO: "🟡",
    Clasificacion.CARO: "🔴",
}


def _dinero(x: float | None) -> str:
    return "—" if x is None else formato_dinero(x)


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f} %".replace(".", ",")


def imprimir(r: ResultadoAnalisis) -> None:
    print()
    print(f"Producto consultado: {r.marca} {r.detalle}")
    print(f"Precio a analizar:   {_dinero(r.precio)}")
    if r.producto:
        print(f"Comparado con:       {r.producto.nombre}")
    print()

    if r.distribuidores:
        print("Precios actuales por distribuidor")
        ancho = max(len(d.distribuidor) for d in r.distribuidores)
        for d in r.distribuidores:
            print(
                f"  {d.distribuidor:<{ancho}}  {_dinero(d.precio):>16}  "
                f"({d.fecha.isoformat()})  tu precio: {_pct(d.diferencia_pct)}"
            )
        print(f"  {'Promedio':<{ancho}}  {_dinero(r.promedio_distribuidores):>16}")
        print()

    h = r.historico
    if h:
        print(f"Historial ({h.registros} registros, {h.desde} a {h.hasta})")
        print(f"  Promedio {_dinero(h.promedio)} · Mediana {_dinero(h.mediana)}")
        print(f"  Mínimo {_dinero(h.minimo)} · Máximo {_dinero(h.maximo)}")
        if h.tendencia_pct is not None:
            print(f"  Tendencia último mes: {_pct(h.tendencia_pct)}")
        print()

    if r.clasificacion:
        print(f"Precio de referencia: {_dinero(r.precio_referencia)}")
        print(f"  vs. distribuidores: {_pct(r.diferencia_vs_distribuidores_pct)}")
        print(f"  vs. histórico:      {_pct(r.diferencia_vs_historico_pct)}")
        print()
        print(f"RESULTADO: {ICONOS[r.clasificacion]} {r.clasificacion.value.upper()} "
              f"({_pct(r.diferencia_pct)})   confianza: {r.confianza}")
        for c in r.conclusiones:
            print(f"  • {c}")
    else:
        print("RESULTADO: no se pudo analizar (sin datos suficientes).")
    for a in r.avisos:
        print(f"  ⚠ {a}")
    print()


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analizador_precios",
        description="Analiza si el precio de un producto es muy barato, barato, promedio o caro.",
    )
    p.add_argument("--db", default="precios.db", help="archivo SQLite (por defecto: precios.db)")
    sub = p.add_subparsers(dest="comando", required=True)

    a = sub.add_parser("analizar", help="analiza un precio")
    a.add_argument("marca")
    a.add_argument("detalle")
    a.add_argument("precio", help='por ejemplo 799990, "799.990" o "$ 1.299,90"')
    a.add_argument("--registrar", metavar="DISTRIBUIDOR",
                   help="guarda además este precio en el historial con ese distribuidor")
    a.add_argument("--mercadolibre", metavar="SITIO",
                   help="consulta también Mercado Libre (MLA, MLC, MLM, MCO, MPE, MLU…); "
                        "requiere la variable ML_ACCESS_TOKEN")
    a.add_argument("--json", action="store_true", help="salida en JSON")
    a.add_argument("--muy-barato", type=float, default=-15.0, help="umbral %% (por defecto -15)")
    a.add_argument("--barato", type=float, default=-5.0, help="umbral %% (por defecto -5)")
    a.add_argument("--caro", type=float, default=5.0, help="umbral %% (por defecto +5)")
    a.add_argument("--dias-vigencia", type=int, default=30,
                   help="antigüedad máxima de un precio de distribuidor (por defecto 30)")

    g = sub.add_parser("agregar", help="registra el precio de un distribuidor")
    g.add_argument("marca")
    g.add_argument("detalle")
    g.add_argument("distribuidor")
    g.add_argument("precio")
    g.add_argument("--fecha", type=date.fromisoformat, help="AAAA-MM-DD (por defecto hoy)")

    i = sub.add_parser("importar", help="importa precios desde CSV")
    i.add_argument("archivo", help="columnas: marca,detalle,distribuidor,precio,fecha")

    sub.add_parser("productos", help="lista los productos registrados")

    h = sub.add_parser("historial", help="muestra los precios registrados de un producto")
    h.add_argument("marca")
    h.add_argument("detalle")

    sub.add_parser("demo", help="carga datos de ejemplo ficticios")

    w = sub.add_parser("web", help="inicia la interfaz web")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--puerto", type=int, default=8000)
    w.add_argument("--mercadolibre", metavar="SITIO", help="consulta también Mercado Libre")
    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    db = BaseDatos(args.db)
    try:
        return _ejecutar(args, db)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.cerrar()


def _ejecutar(args, db: BaseDatos) -> int:
    if args.comando == "analizar":
        fuentes = [MercadoLibre(args.mercadolibre)] if args.mercadolibre else []
        analizador = AnalizadorPrecios(
            db,
            fuentes=fuentes,
            umbrales=Umbrales(args.muy_barato, args.barato, args.caro),
            dias_vigencia=args.dias_vigencia,
        )
        precio = leer_precio(args.precio)
        r = analizador.analizar(args.marca, args.detalle, precio)
        if args.registrar:
            producto = r.producto or db.obtener_o_crear_producto(args.marca, args.detalle)
            db.agregar_precio(producto, args.registrar, precio)
        if args.json:
            print(json.dumps(r.a_dict(), ensure_ascii=False, indent=2))
        else:
            imprimir(r)
        return 0 if r.clasificacion else 2

    if args.comando == "agregar":
        producto = db.obtener_o_crear_producto(args.marca, args.detalle)
        reg = db.agregar_precio(producto, args.distribuidor, leer_precio(args.precio), args.fecha)
        print(f"Registrado: {producto.nombre} · {reg.distribuidor} · {_dinero(reg.precio)} · {reg.fecha}")
        return 0

    if args.comando == "importar":
        print(f"Importados {db.importar_csv(args.archivo)} precios.")
        return 0

    if args.comando == "productos":
        productos = db.listar_productos()
        if not productos:
            print("No hay productos. Usa 'agregar', 'importar' o 'demo'.")
        for p in productos:
            print(f"  {p.marca} · {p.detalle}")
        return 0

    if args.comando == "historial":
        encontrados = db.buscar_productos(args.marca, args.detalle, limite=1)
        if not encontrados:
            print("Producto no encontrado.")
            return 1
        producto = encontrados[0][0]
        print(producto.nombre)
        for reg in db.precios_de(producto):
            print(f"  {reg.fecha}  {reg.distribuidor:<30} {_dinero(reg.precio):>16}  ({reg.fuente})")
        return 0

    if args.comando == "demo":
        n = cargar_demo(db)
        print(f"Cargados {n} precios de ejemplo (ficticios) en {db.ruta}.")
        return 0

    if args.comando == "web":
        from .web import servir

        fuentes = [MercadoLibre(args.mercadolibre)] if args.mercadolibre else []
        servir(db, args.host, args.puerto, AnalizadorPrecios(db, fuentes=fuentes))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

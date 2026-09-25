"""Recolección diaria de precios para el catálogo de Precio Justo.

Lee los productos exportados de la base de datos (un JSON por producto, como
los guarda ``ArtifactData list`` con ``out_dir``), busca cada uno en todas las
tiendas y escribe los documentos actualizados más un resumen:

    python -m analizador_precios.recolector --catalogo exportado/productos --salida actualizados

    actualizados/<id>.json    documento completo del producto, listo para guardar
    actualizados/_resumen.json estado por tienda, para el documento estado/recoleccion

Si se vuelve a ejecutar el mismo día, reemplaza los precios automáticos de
ese día en lugar de duplicarlos. Los productos de ejemplo (demo) se omiten.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .tiendas import TIENDAS, ErrorTienda, Tienda, mejor_oferta

ZONA = ZoneInfo("America/Santiago")
FUENTE = "auto"
DIAS_A_CONSERVAR = 730
MAX_BYTES_DOC = 240_000  # el límite de la base es 256 KiB por documento


def hoy_chile() -> date:
    return datetime.now(ZONA).date()


def referencia(doc: dict, fecha: date) -> float | None:
    """Mediana de los últimos 60 días, para descartar accesorios y packs."""
    desde = (fecha - timedelta(days=60)).isoformat()
    recientes = [r["p"] for r in doc.get("precios", []) if r.get("f", "") >= desde]
    return statistics.median(recientes) if recientes else None


def fusionar(doc: dict, nuevos: list[dict], fecha: date) -> dict:
    """Agrega los precios del día, reemplazando los automáticos de esa misma fecha."""
    dia = fecha.isoformat()
    tiendas_nuevas = {n["d"] for n in nuevos}
    precios = [
        r for r in doc.get("precios", [])
        if not (r.get("f") == dia and r.get("o") == FUENTE and r.get("d") in tiendas_nuevas)
    ]
    precios += nuevos
    limite = (fecha - timedelta(days=DIAS_A_CONSERVAR)).isoformat()
    precios = sorted((r for r in precios if r.get("f", "") >= limite), key=lambda r: r["f"])
    salida = {**doc, "precios": precios}
    while len(json.dumps(salida, ensure_ascii=False).encode()) > MAX_BYTES_DOC and precios:
        precios = precios[max(1, len(precios) // 20):]  # descarta el 5 % más antiguo
        salida["precios"] = precios
    return salida


def buscar_en_tienda(tienda: Tienda, doc: dict, fecha: date) -> tuple[dict | None, str | None]:
    """Devuelve (registro, error). Sin coincidencia: (None, None)."""
    consulta = f"{doc['marca']} {doc['detalle']}"
    try:
        productos = tienda.buscar_crudo(consulta)
    except ErrorTienda as e:
        return None, str(e)[:300]
    oferta = mejor_oferta(doc["marca"], doc["detalle"], productos, tienda.nombre, referencia(doc, fecha))
    if not oferta:
        return None, None
    reg = {"d": tienda.nombre, "p": oferta.precio, "f": fecha.isoformat(), "o": FUENTE, "t": oferta.titulo[:160]}
    if oferta.url:
        reg["u"] = oferta.url[:400]
    return reg, None


def recolectar(docs: dict[str, dict], fecha: date, tiendas: dict[str, Tienda] | None = None,
               pausa: float = 1.0) -> tuple[dict[str, dict], dict]:
    tiendas = tiendas or TIENDAS
    reales = {k: v for k, v in docs.items() if not v.get("demo")}
    estado = {n: {"encontrados": 0, "sin_coincidencia": 0, "errores": 0, "ultimo_error": None} for n in tiendas}
    nuevos: dict[str, list[dict]] = {k: [] for k in reales}

    def trabajar_tienda(nombre: str) -> None:
        # Una tienda a la vez por hilo, con pausa entre búsquedas para no saturarla.
        for pid, doc in reales.items():
            reg, error = buscar_en_tienda(tiendas[nombre], doc, fecha)
            e = estado[nombre]
            if error:
                e["errores"] += 1
                e["ultimo_error"] = error
            elif reg:
                e["encontrados"] += 1
                nuevos[pid].append(reg)
            else:
                e["sin_coincidencia"] += 1
            time.sleep(pausa)

    with ThreadPoolExecutor(max_workers=len(tiendas)) as ex:
        list(ex.map(trabajar_tienda, tiendas))

    actualizados = {pid: fusionar(reales[pid], regs, fecha) for pid, regs in nuevos.items() if regs}
    resumen = {
        "fecha": fecha.isoformat(),
        "ejecutado": datetime.now(ZONA).isoformat(timespec="seconds"),
        "productos": len(reales),
        "productos_actualizados": len(actualizados),
        "precios_nuevos": sum(len(v) for v in nuevos.values()),
        "tiendas": estado,
    }
    return actualizados, resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogo", required=True, help="carpeta con un JSON por producto")
    ap.add_argument("--salida", required=True, help="carpeta donde escribir los documentos actualizados")
    ap.add_argument("--fecha", type=date.fromisoformat, help="AAAA-MM-DD (por defecto, hoy en Chile)")
    ap.add_argument("--tiendas", help="lista separada por comas (por defecto, todas)")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre búsquedas en una misma tienda")
    a = ap.parse_args(argv)

    fecha = a.fecha or hoy_chile()
    docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(a.catalogo).glob("*.json"))}
    tiendas = TIENDAS
    if a.tiendas:
        pedidas = [t.strip() for t in a.tiendas.split(",")]
        faltan = [t for t in pedidas if t not in TIENDAS]
        if faltan:
            ap.error(f"tiendas desconocidas: {faltan}; disponibles: {list(TIENDAS)}")
        tiendas = {t: TIENDAS[t] for t in pedidas}

    actualizados, resumen = recolectar(docs, fecha, tiendas, a.pausa)
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    for pid, doc in actualizados.items():
        (salida / f"{pid}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (salida / "_resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

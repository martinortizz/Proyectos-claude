"""Persistencia en SQLite: catálogo de productos e historial de precios."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable

from .modelos import Producto, RegistroPrecio
from .texto import leer_precio, normalizar, similitud

ESQUEMA = """
CREATE TABLE IF NOT EXISTS productos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    marca       TEXT NOT NULL,
    detalle     TEXT NOT NULL,
    clave       TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS precios (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id  INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    distribuidor TEXT NOT NULL,
    precio       REAL NOT NULL CHECK (precio > 0),
    fecha        TEXT NOT NULL,
    fuente       TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_precios_producto ON precios(producto_id, fecha);
"""


class BaseDatos:
    def __init__(self, ruta: str | Path = "precios.db"):
        self.ruta = str(ruta)
        self.conn = sqlite3.connect(self.ruta, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(ESQUEMA)

    def cerrar(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- productos

    @staticmethod
    def _clave(marca: str, detalle: str) -> str:
        return normalizar(f"{marca} {detalle}")

    def obtener_o_crear_producto(self, marca: str, detalle: str) -> Producto:
        marca, detalle = marca.strip(), detalle.strip()
        if not marca or not detalle:
            raise ValueError("La marca y el detalle del producto son obligatorios.")
        clave = self._clave(marca, detalle)
        fila = self.conn.execute("SELECT * FROM productos WHERE clave = ?", (clave,)).fetchone()
        if fila:
            return self._producto(fila)
        cur = self.conn.execute(
            "INSERT INTO productos (marca, detalle, clave) VALUES (?, ?, ?)",
            (marca, detalle, clave),
        )
        self.conn.commit()
        return Producto(cur.lastrowid, marca, detalle)

    def listar_productos(self) -> list[Producto]:
        filas = self.conn.execute("SELECT * FROM productos ORDER BY marca, detalle")
        return [self._producto(f) for f in filas]

    def buscar_productos(
        self, marca: str, detalle: str, minimo: float = 0.55, limite: int = 5
    ) -> list[tuple[Producto, float]]:
        """Productos del catálogo parecidos a marca + detalle, del más parecido al menos.

        La marca debe coincidir (de forma aproximada) para evitar comparar,
        por ejemplo, un televisor Samsung con uno LG del mismo tamaño.
        """
        marca_n = normalizar(marca)
        consulta = f"{marca} {detalle}"
        candidatos = []
        for p in self.listar_productos():
            if similitud(marca_n, p.marca) < 0.8 and marca_n not in normalizar(p.marca):
                continue
            puntaje = similitud(consulta, p.nombre)
            if puntaje >= minimo:
                candidatos.append((p, round(puntaje, 3)))
        candidatos.sort(key=lambda x: x[1], reverse=True)
        return candidatos[:limite]

    @staticmethod
    def _producto(fila: sqlite3.Row) -> Producto:
        return Producto(fila["id"], fila["marca"], fila["detalle"])

    # ------------------------------------------------------------------ precios

    def agregar_precio(
        self,
        producto: Producto,
        distribuidor: str,
        precio: float,
        fecha: date | None = None,
        fuente: str = "manual",
    ) -> RegistroPrecio:
        if precio <= 0:
            raise ValueError("El precio debe ser mayor que cero.")
        distribuidor = distribuidor.strip()
        if not distribuidor:
            raise ValueError("El distribuidor es obligatorio.")
        fecha = fecha or date.today()
        self.conn.execute(
            "INSERT INTO precios (producto_id, distribuidor, precio, fecha, fuente) "
            "VALUES (?, ?, ?, ?, ?)",
            (producto.id, distribuidor, float(precio), fecha.isoformat(), fuente),
        )
        self.conn.commit()
        return RegistroPrecio(producto.id, distribuidor, float(precio), fecha, fuente)

    def precios_de(self, producto: Producto) -> list[RegistroPrecio]:
        filas = self.conn.execute(
            "SELECT * FROM precios WHERE producto_id = ? ORDER BY fecha",
            (producto.id,),
        )
        return [
            RegistroPrecio(
                f["producto_id"],
                f["distribuidor"],
                f["precio"],
                date.fromisoformat(f["fecha"]),
                f["fuente"],
            )
            for f in filas
        ]

    # ------------------------------------------------------------- importación

    def importar_csv(self, ruta: str | Path) -> int:
        """Importa precios desde un CSV con columnas:

        marca, detalle, distribuidor, precio, fecha (AAAA-MM-DD, opcional)
        """
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            return self.importar_filas(csv.DictReader(f), fuente="csv")

    def importar_filas(self, filas: Iterable[dict], fuente: str = "csv") -> int:
        n = 0
        for i, fila in enumerate(filas, start=2):
            try:
                producto = self.obtener_o_crear_producto(fila["marca"], fila["detalle"])
                fecha_txt = (fila.get("fecha") or "").strip()
                fecha = date.fromisoformat(fecha_txt) if fecha_txt else None
                precio = leer_precio(fila["precio"])
                self.agregar_precio(producto, fila["distribuidor"], precio, fecha, fuente)
            except (KeyError, ValueError) as e:
                raise ValueError(f"Fila {i} inválida: {e}") from e
            n += 1
        return n

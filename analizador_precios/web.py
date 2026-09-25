"""Interfaz web mínima (solo biblioteca estándar)."""

from __future__ import annotations

import json
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .analisis import AnalizadorPrecios
from .db import BaseDatos
from .texto import leer_precio

ESTATICOS = Path(__file__).parent / "static"


def crear_manejador(db: BaseDatos, analizador: AnalizadorPrecios):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, formato, *args):  # silencia el log por petición
            pass

        def _responder(self, estado: int, cuerpo: bytes, tipo: str) -> None:
            self.send_response(estado)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, datos, estado: int = HTTPStatus.OK) -> None:
            self._responder(estado, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _leer_json(self) -> dict:
            largo = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(largo) or b"{}")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                html = (ESTATICOS / "index.html").read_bytes()
                return self._responder(HTTPStatus.OK, html, "text/html; charset=utf-8")
            if url.path == "/api/productos":
                return self._json([
                    {"id": p.id, "marca": p.marca, "detalle": p.detalle}
                    for p in db.listar_productos()
                ])
            if url.path == "/api/historial":
                q = parse_qs(url.query)
                encontrados = db.buscar_productos(q.get("marca", [""])[0], q.get("detalle", [""])[0], limite=1)
                if not encontrados:
                    return self._json([])
                return self._json([
                    {"fecha": r.fecha.isoformat(), "distribuidor": r.distribuidor, "precio": r.precio}
                    for r in db.precios_de(encontrados[0][0])
                ])
            self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            try:
                datos = self._leer_json()
                if self.path == "/api/analizar":
                    precio = leer_precio(datos["precio"])
                    r = analizador.analizar(datos["marca"], datos["detalle"], precio)
                    distribuidor = (datos.get("registrar") or "").strip()
                    if distribuidor:
                        producto = r.producto or db.obtener_o_crear_producto(datos["marca"], datos["detalle"])
                        db.agregar_precio(producto, distribuidor, precio)
                    return self._json(r.a_dict())
                if self.path == "/api/precios":
                    producto = db.obtener_o_crear_producto(datos["marca"], datos["detalle"])
                    fecha = date.fromisoformat(datos["fecha"]) if datos.get("fecha") else None
                    reg = db.agregar_precio(producto, datos["distribuidor"], leer_precio(datos["precio"]), fecha)
                    return self._json({"ok": True, "fecha": reg.fecha.isoformat()}, HTTPStatus.CREATED)
                self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)
            except KeyError as e:
                self._json({"error": f"Falta el campo {e}"}, HTTPStatus.BAD_REQUEST)
            except (ValueError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    return Manejador


def servir(db: BaseDatos, host: str = "127.0.0.1", puerto: int = 8000,
           analizador: AnalizadorPrecios | None = None) -> None:
    analizador = analizador or AnalizadorPrecios(db)
    servidor = HTTPServer((host, puerto), crear_manejador(db, analizador))
    print(f"Analizador de precios en http://{host}:{puerto}  (Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()

"""Motor de análisis: compara un precio con distribuidores e historial."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import Enum

from .db import BaseDatos
from .fuentes import FuentePrecios
from .modelos import Producto, RegistroPrecio
from .texto import formato_dinero as _d


class Clasificacion(str, Enum):
    MUY_BARATO = "Muy barato"
    BARATO = "Barato"
    PROMEDIO = "Precio promedio"
    CARO = "Más caro que el promedio"


@dataclass(frozen=True)
class Umbrales:
    """Diferencias porcentuales respecto del precio de referencia.

    Con los valores por defecto:
      diferencia <= -15 %       -> Muy barato
      -15 % < diferencia <= -5 % -> Barato
      -5 % < diferencia <= +5 %  -> Precio promedio
      diferencia > +5 %          -> Más caro que el promedio
    """

    muy_barato: float = -15.0
    barato: float = -5.0
    caro: float = 5.0

    def __post_init__(self):
        if not (self.muy_barato < self.barato < 0 < self.caro):
            raise ValueError("Se requiere muy_barato < barato < 0 < caro.")

    def clasificar(self, diferencia_pct: float) -> Clasificacion:
        if diferencia_pct <= self.muy_barato:
            return Clasificacion.MUY_BARATO
        if diferencia_pct <= self.barato:
            return Clasificacion.BARATO
        if diferencia_pct <= self.caro:
            return Clasificacion.PROMEDIO
        return Clasificacion.CARO


@dataclass
class PrecioDistribuidor:
    distribuidor: str
    precio: float
    fecha: date
    diferencia_pct: float  # precio del usuario respecto de este distribuidor


@dataclass
class ResumenHistorico:
    registros: int
    desde: date
    hasta: date
    promedio: float
    mediana: float
    minimo: float
    maximo: float
    desviacion: float
    percentil_usuario: float  # % de registros históricos más baratos que el precio ingresado
    tendencia_pct: float | None  # variación del último mes vs. el período anterior


@dataclass
class ResultadoAnalisis:
    marca: str
    detalle: str
    precio: float
    producto: Producto | None = None
    coincidencias: list[tuple[Producto, float]] = field(default_factory=list)
    distribuidores: list[PrecioDistribuidor] = field(default_factory=list)
    promedio_distribuidores: float | None = None
    historico: ResumenHistorico | None = None
    precio_referencia: float | None = None
    diferencia_pct: float | None = None
    diferencia_vs_distribuidores_pct: float | None = None
    diferencia_vs_historico_pct: float | None = None
    clasificacion: Clasificacion | None = None
    confianza: str = "sin datos"
    conclusiones: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["clasificacion"] = self.clasificacion.value if self.clasificacion else None
        d["coincidencias"] = [
            {"producto": asdict(p), "similitud": s} for p, s in self.coincidencias
        ]
        return _fechas_a_texto(d)


def _fechas_a_texto(obj):
    if isinstance(obj, dict):
        return {k: _fechas_a_texto(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fechas_a_texto(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _p(x: float, signo: bool = False) -> str:
    return (f"{x:+.1f}" if signo else f"{x:.1f}").replace(".", ",")


def _pct(precio: float, referencia: float) -> float:
    return round((precio - referencia) / referencia * 100, 2)


class AnalizadorPrecios:
    def __init__(
        self,
        db: BaseDatos,
        fuentes: list[FuentePrecios] | None = None,
        umbrales: Umbrales | None = None,
        dias_vigencia: int = 30,
        dias_historial: int = 365,
        peso_distribuidores: float = 0.6,
    ):
        """
        dias_vigencia: antigüedad máxima de un precio para considerarlo el
            precio actual de un distribuidor.
        dias_historial: ventana de tiempo del historial.
        peso_distribuidores: peso de los precios actuales en el precio de
            referencia (el resto corresponde al promedio histórico).
        """
        if not 0 <= peso_distribuidores <= 1:
            raise ValueError("peso_distribuidores debe estar entre 0 y 1.")
        self.db = db
        self.fuentes = fuentes or []
        self.umbrales = umbrales or Umbrales()
        self.dias_vigencia = dias_vigencia
        self.dias_historial = dias_historial
        self.peso_distribuidores = peso_distribuidores

    # ------------------------------------------------------------------ público

    def analizar(
        self,
        marca: str,
        detalle: str,
        precio: float,
        hoy: date | None = None,
        consultar_fuentes: bool = True,
    ) -> ResultadoAnalisis:
        if precio <= 0:
            raise ValueError("El precio debe ser mayor que cero.")
        hoy = hoy or date.today()
        r = ResultadoAnalisis(marca.strip(), detalle.strip(), float(precio))

        if consultar_fuentes and self.fuentes:
            self._actualizar_desde_fuentes(r, hoy)

        r.coincidencias = self.db.buscar_productos(marca, detalle)
        if not r.coincidencias:
            r.avisos.append(
                "No hay datos de este producto. Registra precios de distribuidores "
                "(comando 'agregar' o 'importar') para poder analizarlo."
            )
            return r
        r.producto = r.coincidencias[0][0]
        if r.coincidencias[0][1] < 0.85:
            r.avisos.append(
                f"No hubo coincidencia exacta; se comparó con «{r.producto.nombre}». "
                "Verifica que sea el mismo producto."
            )

        registros = self.db.precios_de(r.producto)
        self._comparar_distribuidores(r, registros, hoy)
        self._comparar_historico(r, registros, hoy)
        self._clasificar(r)
        return r

    # ----------------------------------------------------------------- privado

    def _actualizar_desde_fuentes(self, r: ResultadoAnalisis, hoy: date) -> None:
        for fuente in self.fuentes:
            try:
                ofertas = fuente.buscar(r.marca, r.detalle)
            except Exception as e:  # una fuente caída no debe impedir el análisis
                r.avisos.append(f"No se pudo consultar {fuente.nombre}: {e}")
                continue
            if not ofertas:
                continue
            parecidos = self.db.buscar_productos(r.marca, r.detalle, minimo=0.85, limite=1)
            producto = (
                parecidos[0][0]
                if parecidos
                else self.db.obtener_o_crear_producto(r.marca, r.detalle)
            )
            ya_registrados = {
                (p.distribuidor, p.precio) for p in self.db.precios_de(producto) if p.fecha == hoy
            }
            for o in ofertas:
                if (o.distribuidor, o.precio) not in ya_registrados:
                    self.db.agregar_precio(producto, o.distribuidor, o.precio, hoy, fuente.nombre)

    def _comparar_distribuidores(
        self, r: ResultadoAnalisis, registros: list[RegistroPrecio], hoy: date
    ) -> None:
        limite = hoy - timedelta(days=self.dias_vigencia)
        ultimo: dict[str, RegistroPrecio] = {}
        for reg in registros:  # vienen ordenados por fecha
            if limite <= reg.fecha <= hoy:
                ultimo[reg.distribuidor] = reg
        r.distribuidores = sorted(
            (
                PrecioDistribuidor(reg.distribuidor, reg.precio, reg.fecha, _pct(r.precio, reg.precio))
                for reg in ultimo.values()
            ),
            key=lambda d: d.precio,
        )
        if r.distribuidores:
            r.promedio_distribuidores = round(
                statistics.fmean(d.precio for d in r.distribuidores), 2
            )
            r.diferencia_vs_distribuidores_pct = _pct(r.precio, r.promedio_distribuidores)
        else:
            r.avisos.append(
                f"Ningún distribuidor tiene precios de los últimos {self.dias_vigencia} días."
            )

    def _comparar_historico(
        self, r: ResultadoAnalisis, registros: list[RegistroPrecio], hoy: date
    ) -> None:
        desde = hoy - timedelta(days=self.dias_historial)
        hist = [reg for reg in registros if desde <= reg.fecha <= hoy]
        if not hist:
            r.avisos.append(f"Sin historial de precios en los últimos {self.dias_historial} días.")
            return
        precios = [reg.precio for reg in hist]
        mas_baratos = sum(1 for p in precios if p < r.precio)
        iguales = sum(1 for p in precios if p == r.precio)
        r.historico = ResumenHistorico(
            registros=len(hist),
            desde=hist[0].fecha,
            hasta=hist[-1].fecha,
            promedio=round(statistics.fmean(precios), 2),
            mediana=round(statistics.median(precios), 2),
            minimo=min(precios),
            maximo=max(precios),
            desviacion=round(statistics.pstdev(precios), 2),
            percentil_usuario=round((mas_baratos + iguales / 2) / len(precios) * 100, 1),
            tendencia_pct=self._tendencia(hist, hoy),
        )
        r.diferencia_vs_historico_pct = _pct(r.precio, r.historico.promedio)

    def _tendencia(self, hist: list[RegistroPrecio], hoy: date) -> float | None:
        corte = hoy - timedelta(days=self.dias_vigencia)
        recientes = [x.precio for x in hist if x.fecha > corte]
        anteriores = [x.precio for x in hist if x.fecha <= corte]
        if not recientes or not anteriores:
            return None
        return _pct(statistics.fmean(recientes), statistics.fmean(anteriores))

    def _clasificar(self, r: ResultadoAnalisis) -> None:
        dist, hist = r.promedio_distribuidores, r.historico
        if dist is not None and hist is not None:
            w = self.peso_distribuidores
            r.precio_referencia = round(w * dist + (1 - w) * hist.promedio, 2)
        elif dist is not None:
            r.precio_referencia = dist
        elif hist is not None:
            r.precio_referencia = hist.promedio
        else:
            return

        r.diferencia_pct = _pct(r.precio, r.precio_referencia)
        r.clasificacion = self.umbrales.clasificar(r.diferencia_pct)
        r.confianza = self._confianza(r)
        r.conclusiones = self._conclusiones(r)

    @staticmethod
    def _confianza(r: ResultadoAnalisis) -> str:
        n_dist = len(r.distribuidores)
        n_hist = r.historico.registros if r.historico else 0
        if n_dist >= 3 and n_hist >= 10:
            return "alta"
        if n_dist >= 2 or n_hist >= 5:
            return "media"
        return "baja"

    @staticmethod
    def _conclusiones(r: ResultadoAnalisis) -> list[str]:
        c = []
        dif = r.diferencia_pct
        signo = "por debajo" if dif < 0 else "por encima"
        c.append(
            f"El precio ingresado está {_p(abs(dif))} % {signo} del precio de "
            f"referencia ({_d(r.precio_referencia)})."
        )
        if r.distribuidores:
            mas_barato = r.distribuidores[0]
            if r.precio <= mas_barato.precio:
                c.append(
                    f"Es igual o más bajo que el de todos los distribuidores "
                    f"(el más barato, {mas_barato.distribuidor}, lo vende a {_d(mas_barato.precio)})."
                )
            else:
                mas_caros = sum(1 for d in r.distribuidores if d.precio > r.precio)
                c.append(
                    f"{mas_caros} de {len(r.distribuidores)} distribuidores lo venden más caro. "
                    f"El más barato es {mas_barato.distribuidor} a {_d(mas_barato.precio)}."
                )
        h = r.historico
        if h:
            if r.precio < h.minimo:
                c.append(f"Es más bajo que el mínimo histórico ({_d(h.minimo)}).")
            elif r.precio > h.maximo:
                c.append(f"Supera el máximo histórico ({_d(h.maximo)}).")
            else:
                c.append(
                    f"El {h.percentil_usuario:.0f} % de los precios del historial fue más "
                    f"barato que este (rango {_d(h.minimo)} – {_d(h.maximo)})."
                )
            if h.tendencia_pct is not None and abs(h.tendencia_pct) >= 3:
                sentido = "subiendo" if h.tendencia_pct > 0 else "bajando"
                c.append(
                    f"El precio del producto viene {sentido} ({_p(h.tendencia_pct, True)} % en el último mes)."
                )
        return c

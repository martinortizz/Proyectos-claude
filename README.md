# Analizador de precios

Ingresas **marca**, **detalle del producto** y **precio**. El sistema compara ese precio
con los precios actuales de los distintos distribuidores y con el historial de precios del
producto, y lo clasifica como:

| Resultado | Diferencia con el precio de referencia |
|---|---|
| 🟢🟢 **Muy barato** | 15 % o más por debajo |
| 🟢 **Barato** | entre 5 % y 15 % por debajo |
| 🟡 **Precio promedio** | entre −5 % y +5 % |
| 🔴 **Más caro que el promedio** | más de 5 % por encima |

Solo usa la biblioteca estándar de Python (3.10 o superior). No hace falta instalar nada.

## Versión web en línea

La carpeta `artifact/` tiene una versión de la aplicación que funciona en el navegador y está
publicada en claude.ai: https://claude.ai/artifact/3Ws7PteqZbqM3KavUjBCf5 . Guarda los precios en
una base de datos en la nube y aplica el mismo análisis que la versión en Python.

## Uso rápido

```bash
# 1. Cargar datos de ejemplo (ficticios) para probar
python3 -m analizador_precios demo

# 2. Analizar un precio
python3 -m analizador_precios analizar "Samsung" "Galaxy S24 128GB" "799.990"

# 3. O usar la interfaz web en http://127.0.0.1:8000
python3 -m analizador_precios web
```

Ejemplo de salida:

```
Precios actuales por distribuidor
  TecnoShop          $822.570  (2026-09-25)  tu precio: -2,8 %
  MegaStore          $867.830  (2026-09-25)  tu precio: -7,8 %
  Tienda Uno         $901.680  (2026-09-25)  tu precio: -11,3 %
  ElectroMax         $952.750  (2026-09-25)  tu precio: -16,0 %
  Promedio           $886.207,50

Historial (212 registros, 2025-09-26 a 2026-09-25)
  Promedio $954.855,85 · Mediana $954.295
  Mínimo $766.460 · Máximo $1.074.570
  Tendencia último mes: -4,9 %

Precio de referencia: $913.666,84
  vs. distribuidores: -9,7 %
  vs. histórico:      -16,2 %

RESULTADO: 🟢 BARATO (-12,4 %)   confianza: alta
  • El precio ingresado está 12,4 % por debajo del precio de referencia ($913.666,84).
  • Es igual o más bajo que el de todos los distribuidores (el más barato, TecnoShop, lo vende a $822.570).
  • El 1 % de los precios del historial fue más barato que este (rango $766.460 – $1.074.570).
  • El precio del producto viene bajando (-4,9 % en el último mes).
```

## Cómo se calcula

1. **Búsqueda del producto.** Busca en el catálogo por similitud. No importan las mayúsculas,
   las tildes ni el orden de las palabras, y "128 GB" equivale a "128GB". La marca tiene que
   coincidir. Si no hay una coincidencia exacta, el resultado lo avisa.
2. **Distribuidores.** Toma el último precio de cada distribuidor en los últimos 30 días
   (`--dias-vigencia`) y calcula el promedio. Así cada distribuidor cuenta una sola vez.
3. **Historial.** Usa todos los precios de los últimos 365 días y calcula el promedio, la mediana,
   el mínimo, el máximo, el percentil del precio ingresado y la tendencia del último mes frente al
   período anterior.
4. **Precio de referencia** = 60 % promedio de distribuidores + 40 % promedio histórico. Si falta
   una de las dos fuentes, se usa solo la otra.
5. **Clasificación** según la diferencia porcentual con la referencia. Los umbrales se pueden
   cambiar con `--muy-barato`, `--barato` y `--caro`.
6. **Confianza.** Es *alta* con 3 o más distribuidores y 10 o más registros históricos, *media*
   con 2 distribuidores o 5 registros, y *baja* en los demás casos.

## Cargar precios reales

El sistema analiza los precios que tiene guardados en `precios.db` (SQLite). Hay tres formas de cargarlos:

```bash
# a) Uno por uno
python3 -m analizador_precios agregar "LG" "Smart TV 55 4K" "Tienda X" "429.990" --fecha 2026-09-20

# b) Desde un CSV (columnas: marca,detalle,distribuidor,precio,fecha)
python3 -m analizador_precios importar precios.csv

# c) Al analizar, guardar también el precio que viste
python3 -m analizador_precios analizar "LG" "Smart TV 55 4K" 399990 --registrar "Tienda Y"
```

Cada precio que registras se suma al historial, así que los análisis mejoran con el uso.

### Consulta automática a Mercado Libre (opcional)

```bash
export ML_ACCESS_TOKEN=...   # token de la API de Mercado Libre
python3 -m analizador_precios analizar "Sony" "WH-1000XM5" 299990 --mercadolibre MLC
```

`MLC` = Chile, `MLA` = Argentina, `MLM` = México, `MCO` = Colombia, `MPE` = Perú, `MLU` = Uruguay.
Cada vendedor cuenta como un distribuidor y los precios que encuentra se guardan en el historial.
Si la consulta falla, el análisis sigue con los datos locales y muestra un aviso.

Para agregar otra tienda, implementa una clase con `nombre` y `buscar(marca, detalle) -> list[Oferta]`
(ver `analizador_precios/fuentes.py`) y pásala en `AnalizadorPrecios(db, fuentes=[...])`.

## Otros comandos

```bash
python3 -m analizador_precios productos                       # lista el catálogo
python3 -m analizador_precios historial "Sony" "WH-1000XM5"   # precios registrados
python3 -m analizador_precios analizar ... --json             # resultado en JSON
```

## Estructura

```
analizador_precios/
  analisis.py   motor de análisis y clasificación
  db.py         catálogo e historial (SQLite), importación CSV
  fuentes.py    fuentes externas de precios (Mercado Libre)
  texto.py      normalización, búsqueda aproximada y formato de precios
  cli.py        línea de comandos
  web.py        servidor web y API (/api/analizar, /api/precios, /api/historial)
  static/       interfaz web
  demo.py       datos de ejemplo ficticios
tests/          pruebas (python3 -m unittest)
```

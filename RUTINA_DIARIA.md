# Rutina diaria de precios (23:59, hora de Chile)

Instrucciones para la sesión de Claude que se ejecuta cada noche. Actualiza la base de
datos de la página **Precio Justo** con los precios del día de Paris, Falabella, Ripley,
Líder, Jumbo y Mercado Libre.

- Página: https://claude.ai/artifact/3Ws7PteqZbqM3KavUjBCf5
- Colección de productos: `productos` (un documento por producto)
- Estado de la recolección: colección `estado`, documento `recoleccion`

## Pasos

1. **Fecha.** Anota la fecha de hoy en Chile *antes* de empezar, porque la rutina puede
   terminar después de medianoche:
   `python3 -c "from analizador_precios.recolector import hoy_chile; print(hoy_chile())"`
   Usa esa fecha en `--fecha` en el paso 3.

2. **Exportar el catálogo.** Con la herramienta `ArtifactData`, acción `list`, colección
   `productos`, `out_dir` = `/tmp/precios/exportado` (página con `query.limit` = 1000). Anota el
   `version` de cada documento que muestra el resultado.

3. **Buscar precios.**
   `python3 -m analizador_precios.recolector --catalogo /tmp/precios/exportado/productos --salida /tmp/precios/actualizados --fecha <FECHA>`
   Deja en `/tmp/precios/actualizados/` un JSON por producto actualizado y `_resumen.json`.

4. **Guardar los productos.** Con `ArtifactData`, acción `batch` (hasta 50 escrituras por
   llamada): para cada archivo `<id>.json` de `/tmp/precios/actualizados` salvo `_resumen.json`,
   una escritura `{op: "set", collection: "productos", doc_id: "<id>", file_path: "<ruta>", if_version: <version del paso 2>}`.
   Si el lote falla porque alguien editó un producto mientras corría la rutina, vuelve a
   exportar solo ese producto, repite el paso 3 con él y guárdalo de nuevo.

5. **Guardar el estado.** `ArtifactData` `set` en colección `estado`, documento `recoleccion`,
   con `file_path` = `/tmp/precios/actualizados/_resumen.json`. Hazlo siempre, aunque ninguna
   tienda haya respondido: la página muestra este estado.

## Si una tienda falla

El resumen trae, por tienda, `encontrados`, `sin_coincidencia`, `errores` y `ultimo_error`.

- **Error de red o 403 del proxy de la sesión** (`CONNECT`, `EGRESS_BLOCKED`, `Tunnel connection
  failed: 403`): es la política de red del entorno. No hay nada que corregir en el código;
  el estado guardado lo refleja.
- **La tienda responde pero no trae productos reconocibles, en todos los productos:** el sitio
  probablemente cambió su estructura. Descarga una búsqueda de ejemplo, ubica dónde vienen
  ahora los productos y ajusta solo esa tienda en `analizador_precios/tiendas.py`. Agrega un
  caso a `tests/test_tiendas.py`, ejecuta `python3 -m unittest` y, si todo pasa, haz commit y
  push a la rama por defecto del repositorio. Luego vuelve a ejecutar el paso 3 solo con esa
  tienda (`--tiendas "<Nombre>"`) y guarda los resultados.
- No toques productos marcados con `demo: true`: el recolector ya los omite.

## Al terminar

Responde con un resumen breve: fecha, productos actualizados, precios nuevos y el estado
de cada tienda.

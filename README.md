# Acotamiento automático de polígonos MH (CABA)

Herramienta que dimensiona automáticamente los polígonos de **DOMINIO** y
**SUPERFICIE** de un plano de Mensura y Propiedad Horizontal (MH) de la Ciudad
Autónoma de Buenos Aires, siguiendo la convención real de las plantillas
oficiales CABA (*Detalle_Layers_Bloques4.pdf* v4): el mismo criterio de
estilo, posición y legibilidad que usa un agrimensor al acotar un plano a
mano en AutoCAD.

A partir de un DXF "sin acotar" (los polígonos ya dibujados en Model Space,
sobre las capas `M-MH-<piso>-DOMINIO` / `M-MH-<piso>-SUP`), genera un DXF
nuevo con las cotas (entidades `DIMENSION`) agregadas en Paper Space, sobre el
layout del plano, listas para imprimir.

Se puede usar de dos formas:

- **Página web** (`index.html`): cargás el DXF, ves el layout acotado y
  descargás el resultado. Corre entera en el navegador con
  [Pyodide](https://pyodide.org/), así que el plano **no se sube a ningún
  servidor**.
- **Línea de comandos** (`acotar_mh.py`), con Python instalado.

Las dos usan exactamente el mismo código Python.

## Requisito del archivo: DXF 2010

El DXF de entrada tiene que estar guardado como **AutoCAD 2010/LT2010 DXF**
(en AutoCAD: *Guardar como* › tipo *«AutoCAD 2010/LT2010 DXF (\*.dxf)»*, en
formato texto, no binario). Otras versiones se rechazan al cargar con un
mensaje que lo explica. El motivo está en
[`docs/REGLA-ACOTAMIENTO.md`](docs/REGLA-ACOTAMIENTO.md#archivo-de-entrada-dxf-2010).

## Qué resuelve

- Elige automáticamente el estilo de cota correcto (`MH-Unidad-*`,
  `MH-Común-*`, `Lado-Polígono-Superficie`) según la capa y el color del
  polígono.
- Decide si el texto va con sobrelínea (`...-Arriba`) o subrayado
  (`...-Abajo`) según la inclinación real del lado, para que la lectura sea
  siempre natural (nunca "cabeza abajo" para quien mira el plano).
- Ubica el texto de cada cota **dentro** del polígono correspondiente (o del
  lado de afuera para columnas), evitando que se superponga con el borde o
  con otra cota vecina.
- Evita duplicar una cota cuando el mismo lado ya está acotado por DOMINIO y
  por SUPERFICIE.
- Implementa el caso especial de columnas / muro común exterior ("tubo y
  col"): mismo estilo que "Común" pero espejado, con el texto hacia afuera.
- Corrige varias limitaciones de la librería `ezdxf` para que el resultado
  sea indistinguible de una cota dibujada a mano en AutoCAD (sin flechas
  fantasma, con el prefijo de sobre/subrayado visible en Propiedades, etc.).

La lógica completa está documentada en
[`docs/REGLA-ACOTAMIENTO.md`](docs/REGLA-ACOTAMIENTO.md).

## Estructura del repositorio

```
index.html               Página: presentación + herramienta
app/
  app.js                 Interfaz: carga, opciones, visor con zoom, descarga
  worker.js              Corre Pyodide (Python en el navegador) en segundo plano
  styles.css
  fuentes/               Fuente DejaVu Sans Condensed para la vista previa
acotar_mh.py             Lógica de acotamiento (también es la CLI)
visor_svg.py             Renderiza el layout a SVG para la vista previa
ejemplos/
  generar_ejemplo.py     Genera un plano sintético de prueba
  ejemplo-sin-acotar.dxf El ejemplo que usa el botón "Probar con un ejemplo"
docs/REGLA-ACOTAMIENTO.md
```

## Publicar la página en GitHub Pages

1. Subí el contenido de esta carpeta a un repositorio de GitHub (rama `main`).
   `index.html` tiene que quedar en la **raíz** del repositorio, no dentro de
   una subcarpeta.
2. En el repositorio: **Settings › Pages › Build and deployment**, elegí
   *Deploy from a branch*, rama `main`, carpeta `/ (root)`, y guardá.
3. En uno o dos minutos la página queda en
   `https://<tu-usuario>.github.io/<nombre-del-repo>/`.

No hace falta ningún servidor ni paso de compilación. El archivo `.nojekyll`
evita que GitHub procese los archivos con Jekyll.

La primera vez que alguien abre la página, el navegador descarga Python y las
librerías (unos 15–20 MB desde el CDN de Pyodide y PyPI); después quedan en
caché. Se necesita conexión a internet para esa descarga, pero el plano nunca
sale de la computadora.

### Probarla localmente

Los navegadores no permiten los Web Workers abriendo `index.html` con doble
clic (`file://`). Levantá un servidor local desde la carpeta del repo:

```bash
python -m http.server 8000
```

y abrí `http://localhost:8000`.

## Uso desde la línea de comandos

Requiere Python 3.9+ y las dependencias:

```bash
pip install -r requirements.txt
python acotar_mh.py ENTRADA.dxf SALIDA.dxf
```

Por ejemplo:

```bash
python acotar_mh.py "MH LE BRETON sin acotar.dxf" "MH LE BRETON ACOTADO.dxf"
```

| Opción | Default | Descripción |
|---|---|---|
| `--pisos` | se detectan del archivo | Pisos a procesar (deben existir las capas `M-MH-<piso>-DOMINIO` / `-SUP`) |
| `--layout` | `FORMATO-A1`, o el que mejor coincida | Layout (paper space) donde está el VIEWPORT de cada piso |

El script imprime un resumen de cuántas cotas generó por estilo, cuántas
aristas de SUPERFICIE se omitieron por coincidir con DOMINIO, las cotas que
conviene revisar a mano y el resultado de la auditoría de `ezdxf`.

Para una vista previa del layout en SVG:

```bash
python visor_svg.py "MH LE BRETON ACOTADO.dxf" FORMATO-A1 vista.svg
```

## Supuestos sobre el archivo de entrada

- DXF 2010 en formato texto.
- Los polígonos de DOMINIO y SUPERFICIE son `LWPOLYLINE` cerradas, en espacio
  modelo, sobre las capas `M-MH-<piso>-DOMINIO` y `M-MH-<piso>-SUP`.
- El color de la polilínea indica su tipo, según la convención CABA: verde
  (3) = Unidad, amarillo (2) = Común, rojo (1) = cubierta (no se acota),
  blanco (7) = muro común exterior / columna (se acota con la regla
  espejada), magenta (6) = polígono de apoyo para planillas (nunca se
  acota).
- El layout de papel tiene un `VIEWPORT` por piso, identificable porque es el
  único cuya lista de capas congeladas **no** incluye la capa
  `M-MH-<piso>-DOMINIO` de ese piso.
- Los DIMSTYLE `MH-Unidad-Abajo/Arriba`, `MH-Común-Abajo/Arriba` y
  `Lado-Polígono-Superficie` ya existen en el archivo (vienen de la
  plantilla oficial CABA). Si falta alguno, se avisa y no se procesa.

Toda la configuración relevante (colores, offsets, tamaños de texto) está
centralizada al principio de `acotar_mh.py`.

## Limitaciones conocidas

- En aristas muy cortas (del orden de centímetros: nichos, ductos, columnas
  angostas), puede no haber espacio físico para que el rótulo entre completo
  dentro del polígono. En esos casos el texto igual se coloca, pero solo se
  garantiza que su centro caiga dentro del polígono. La página los lista en
  "Para revisar a mano" y los marca con un círculo rojo en la vista previa
  (el círculo no se guarda en el DXF).
- El caso "tubo y columnas" asume que todo polígono color blanco (7) dentro
  de la capa SUP es una columna o muro común exterior.
- La vista previa la dibuja `ezdxf`, no AutoCAD: las fuentes pueden verse
  levemente distintas. Las cotas del DXF descargado son las mismas.
- Herramienta independiente, no oficial del GCBA. Revisá siempre el plano
  antes de presentarlo.

## Licencia

Este repositorio no incluye una licencia todavía. Agregá un archivo `LICENSE`
con la que prefieras (MIT es una opción simple y permisiva).

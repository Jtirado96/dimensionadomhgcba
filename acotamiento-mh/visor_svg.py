"""
visor_svg.py — Renderiza un layout (espacio papel) de un DXF a SVG con el
addon `drawing` de ezdxf, incluido el contenido de cada VIEWPORT y las cotas.

Lo usa la interfaz web para mostrar el plano acotado "como en AutoCAD". Como es
la misma librería que generó el archivo, lo que se ve coincide con lo que se
descarga.

También se puede usar desde la línea de comandos para obtener una vista
previa:
    python visor_svg.py PLANO.dxf FORMATO-A1 vista.svg
"""

import re
import sys

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg

CAPA_MARCAS = "__REVISAR__"
COLOR_MARCAS = "#ff2d55"
FONDOS = {"oscuro": "#212830", "claro": "#ffffff"}

# Sobre fondo claro, los colores ACI puros (amarillo, verde, cian) casi no se
# leen; se oscurecen solo en la vista previa, manteniendo el tono.
COLORES_FONDO_CLARO = {
    "#ffff00": "#a68a00", "#00ff00": "#14912d", "#00ffff": "#008a9e",
    "#ff00ff": "#b0189e", "#ff0000": "#d01c1c",
}


def renderizar_layout(path, layout_name, fondo="oscuro", marcas=None):
    """Devuelve el SVG (str) del layout `layout_name`.

    `marcas` es una lista de puntos (x, y) en espacio papel que se resaltan
    con un círculo rojo (cotas a revisar). Las marcas solo se dibujan en la
    vista previa: no se agregan al DXF descargable."""
    doc = ezdxf.readfile(path)
    paper = doc.layout(layout_name)

    ext = bbox.extents(paper, fast=True)
    if not ext.has_data:
        raise ValueError(f"El layout «{layout_name}» está vacío.")
    ancho, alto = ext.size.x or 1.0, ext.size.y or 1.0

    if marcas:
        doc.layers.add(CAPA_MARCAS, color=1)
        radio = max(ancho, alto) * 0.005
        for x, y in marcas:
            c = paper.add_circle((x, y), radio, dxfattribs={"layer": CAPA_MARCAS})
            c.rgb = (0xFF, 0x2D, 0x55)

    ctx = RenderContext(doc)
    ctx.set_current_layout(paper)
    backend = svg.SVGBackend()
    cfg = config.Configuration(
        background_policy=config.BackgroundPolicy.CUSTOM,
        custom_bg_color=FONDOS.get(fondo, FONDOS["oscuro"]),
        color_policy=config.ColorPolicy.COLOR,
    )
    Frontend(ctx, backend, config=cfg).draw_layout(paper, finalize=True)

    # Página proporcional al contenido del layout, con el lado mayor en 1000 mm.
    k = 1000.0 / max(ancho, alto)
    pagina = layout.Page(ancho * k, alto * k, layout.Units.mm,
                         margins=layout.Margins.all(5))
    texto = backend.get_string(pagina, settings=layout.Settings(fit_page=True),
                               xml_declaration=False)
    return _ajustar_para_pantalla(texto, fondo)


def _ajustar_para_pantalla(texto, fondo):
    """Líneas de grosor constante en pantalla (como AutoCAD con el grosor de
    línea desactivado), sin importar el zoom; marcas de revisión más gruesas;
    y, sobre fondo claro, colores más oscuros para que se lean."""
    def regla(m):
        cuerpo = m.group(0)
        ancho = "2.5px" if COLOR_MARCAS in cuerpo else "1px"
        return re.sub(r"stroke-width: [0-9.]+", f"stroke-width: {ancho}", cuerpo)

    texto = re.sub(r"\.C\d+ \{[^}]*\}", regla, texto)
    if fondo == "claro":
        for original, nuevo in COLORES_FONDO_CLARO.items():
            texto = texto.replace(original, nuevo)
    estilo = "<style>path{vector-effect:non-scaling-stroke}</style>"
    return texto.replace("<defs>", "<defs>" + estilo, 1)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("Uso: python visor_svg.py PLANO.dxf LAYOUT salida.svg")
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        f.write(renderizar_layout(sys.argv[1], sys.argv[2]))

#!/usr/bin/env python3
"""
generar_ejemplo.py — Genera un plano MH de EJEMPLO (sintético, no es un plano
real) en formato DXF 2010, con la misma estructura que espera acotar_mh.py:

- Capas M-MH-<piso>-DOMINIO / M-MH-<piso>-SUP con polígonos cerrados en
  espacio modelo, usando la convención de colores CABA.
- Layout FORMATO-A1 (unidades en metros de papel) con un VIEWPORT por piso,
  cada uno con congeladas las capas de los otros pisos.
- DIMSTYLE MH-Unidad-*, MH-Común-* y Lado-Polígono-Superficie.

Sirve para probar la herramienta sin tener un plano propio a mano (la página
web lo ofrece con el botón "Probar con un ejemplo").

Uso:
    python ejemplos/generar_ejemplo.py ejemplos/ejemplo-sin-acotar.dxf
"""

import sys

import ezdxf

PISOS = ["PB", "01P"]
LAYOUT = "FORMATO-A1"
ESCALA = 100  # 1:100


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def crear_estilos(doc):
    base = dict(
        dimasz=0.0, dimse1=1, dimse2=1, dimsd1=1, dimsd2=1, dimtad=0, dimgap=0.0005,
        dimexo=0.0, dimexe=0.0, dimdec=2, dimdsep=46, dimtih=0, dimtoh=0, dimclrt=256,
    )
    for nombre, txt in [
        ("MH-Unidad-Abajo", 0.002), ("MH-Unidad-Arriba", 0.002),
        ("MH-Común-Abajo", 0.0018), ("MH-Común-Arriba", 0.0018),
        ("Lado-Polígono-Superficie", 0.0018),
    ]:
        ds = doc.dimstyles.new(nombre)
        for k, v in base.items():
            ds.dxf.set(k, v)
        ds.dxf.dimtxt = txt


def poly(msp, pts, layer, color):
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer, "color": color})


def piso_pb(msp, ox):
    d, s = "M-MH-PB-DOMINIO", "M-MH-PB-SUP"
    # UF 1 (verde) con un entrante (polígono cóncavo)
    uf1 = [(ox, 0), (ox + 8.66, 0), (ox + 8.66, 10.5), (ox + 5.2, 10.5),
           (ox + 5.2, 8.3), (ox, 8.3)]
    poly(msp, uf1, d, 3)
    # Común (amarillo): pasillo + escalera
    comun = [(ox, 8.3), (ox + 5.2, 8.3), (ox + 5.2, 10.5), (ox + 8.66, 10.5),
             (ox + 8.66, 17.32), (ox + 6.1, 17.32), (ox + 6.1, 12.4), (ox, 12.4)]
    poly(msp, comun, d, 2)
    # UF 2 complementaria (verde) al fondo
    poly(msp, rect(ox, 12.4, ox + 6.1, 17.32), d, 3)

    # SUPERFICIE: locales dentro de la UF 1 (color 4 = cian)
    poly(msp, rect(ox, 0, ox + 4.15, 4.9), s, 4)
    poly(msp, rect(ox + 4.15, 0, ox + 8.66, 4.9), s, 4)
    poly(msp, [(ox, 4.9), (ox + 8.66, 4.9), (ox + 8.66, 10.5), (ox + 5.2, 10.5),
               (ox + 5.2, 8.3), (ox, 8.3)], s, 4)
    # Cubierta (rojo, no se acota) y apoyo (magenta, nunca se acota)
    poly(msp, rect(ox + 0.5, 13.0, ox + 5.6, 16.8), s, 1)
    poly(msp, rect(ox + 1.0, 1.0, ox + 3.0, 3.0), s, 6)
    # Columna (blanco): regla espejada, texto hacia afuera
    poly(msp, rect(ox + 4.0, 7.2, ox + 4.3, 7.5), s, 7)


def piso_01p(msp, ox):
    d, s = "M-MH-01P-DOMINIO", "M-MH-01P-SUP"
    uf3 = [(ox, 0), (ox + 8.66, 0), (ox + 8.66, 10.5), (ox + 5.2, 10.5),
           (ox + 5.2, 8.3), (ox, 8.3)]
    poly(msp, uf3, d, 3)
    comun = [(ox, 8.3), (ox + 5.2, 8.3), (ox + 5.2, 10.5), (ox + 8.66, 10.5),
             (ox + 8.66, 12.4), (ox, 12.4)]
    poly(msp, comun, d, 2)
    # Balcón en ángulo (lado inclinado: prueba Arriba/Abajo con lados oblicuos)
    poly(msp, [(ox, 0), (ox + 8.66, 0), (ox + 7.9, -1.2), (ox + 0.8, -1.2)], d, 3)
    poly(msp, rect(ox, 0, ox + 3.6, 5.3), s, 4)
    poly(msp, [(ox + 3.6, 0), (ox + 8.66, 0), (ox + 8.66, 5.3), (ox + 3.6, 5.3)], s, 4)
    poly(msp, rect(ox + 8.36, 5.3, ox + 8.66, 5.6), s, 7)
    # Ducto angosto: el texto no entra adentro y queda marcado para revisar
    poly(msp, rect(ox + 7.0, 9.0, ox + 7.12, 10.2), s, 4)


def main(salida):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6  # metros
    crear_estilos(doc)
    doc.layers.add("01-P-PLANO-CARATULA", color=7)
    for p in PISOS:
        doc.layers.add(f"M-MH-{p}-DOMINIO", color=3)
        doc.layers.add(f"M-MH-{p}-SUP", color=4)
        doc.layers.add(f"M-MH-{p}-TEXTO", color=7)

    msp = doc.modelspace()
    origenes = {"PB": 0.0, "01P": 30.0}
    piso_pb(msp, origenes["PB"])
    piso_01p(msp, origenes["01P"])
    for p, ox in origenes.items():
        msp.add_text(f"PLANTA {p}", height=0.35,
                     dxfattribs={"layer": f"M-MH-{p}-TEXTO"}).set_placement((ox, 19.0))

    # Layout de papel A1 en metros (0.841 x 0.594)
    paper = doc.layouts.new(LAYOUT)
    doc.layouts.delete("Layout1")  # layout vacío que ezdxf crea por defecto
    car = "01-P-PLANO-CARATULA"
    paper.add_lwpolyline(rect(0, 0, 0.841, 0.594), close=True, dxfattribs={"layer": car})
    paper.add_lwpolyline(rect(0.01, 0.01, 0.831, 0.584), close=True, dxfattribs={"layer": car})
    paper.add_lwpolyline(rect(0.62, 0.01, 0.831, 0.16), close=True, dxfattribs={"layer": car})
    paper.add_text("PLANO DE MENSURA - PH (EJEMPLO)", height=0.006,
                   dxfattribs={"layer": car}).set_placement((0.63, 0.14))
    paper.add_text("Plano sintético para probar el acotamiento", height=0.004,
                   dxfattribs={"layer": car}).set_placement((0.63, 0.125))

    todas = [f"M-MH-{p}-{c}" for p in PISOS for c in ("DOMINIO", "SUP", "TEXTO")]
    for i, (p, ox) in enumerate(origenes.items()):
        alto_papel = 0.24
        view_h = alto_papel * ESCALA
        vp = paper.add_viewport(
            center=(0.17 + i * 0.24, 0.33),
            size=(0.2, alto_papel),
            view_center_point=(ox + 4.33, 8.5),
            view_height=view_h,
            status=2 + i,
        )
        vp.dxf.layer = car
        vp.frozen_layers = [l for l in todas if f"-{p}-" not in l]
        paper.add_text(f"PLANTA {p}  ESC. 1:{ESCALA}", height=0.005,
                       dxfattribs={"layer": car}).set_placement((0.09 + i * 0.24, 0.19))

    doc.saveas(salida)
    print(f"Ejemplo guardado: {salida} (DXF {doc.acad_release})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ejemplo-sin-acotar.dxf")

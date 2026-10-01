#!/usr/bin/env python3
"""
acotar_mh.py — Acotamiento automático de polígonos DOMINIO/SUPERFICIE en planos
MH (Mensura y Propiedad Horizontal) de la Ciudad de Buenos Aires, siguiendo la
convención de las plantillas oficiales CABA (Detalle_Layers_Bloques4.pdf v4).

Lee un DXF "sin acotar" (polígonos dibujados en Model Space, sobre las capas
M-MH-<piso>-DOMINIO / M-MH-<piso>-SUP) y genera un DXF nuevo con cotas DIMENSION
agregadas en Paper Space, sobre la capa 01-P-PLANO-CARATULA del layout
indicado, respetando el estilo de dimensionado real de AutoCAD (MH-Unidad-*,
MH-Común-*, Lado-Polígono-Superficie) y las reglas de legibilidad descriptas en
docs/REGLA-ACOTAMIENTO.md de este repositorio.

El archivo de entrada debe estar guardado como DXF 2010 (AC1024, texto);
cualquier otra versión se rechaza con un mensaje explicativo.

El mismo módulo lo usa la interfaz web (index.html), que lo ejecuta dentro del
navegador con Pyodide: las funciones analizar() y acotar() devuelven
diccionarios con el resultado además de imprimir el informe.

Uso:
    python acotar_mh.py ENTRADA.dxf SALIDA.dxf [opciones]

Ejemplo:
    python acotar_mh.py "MH LE BRETON sin acotar.dxf" "MH LE BRETON ACOTADO.dxf"

Requiere: ezdxf, shapely  (ver requirements.txt)
"""

import argparse
import math
import sys
from collections import Counter

import ezdxf
from shapely.geometry import Point, Polygon

# ---------------------------------------------------------------------------
# Convenciones de capas y colores (Detalle_Layers_Bloques4.pdf v4)
# ---------------------------------------------------------------------------

LAYER_CARATULA = "01-P-PLANO-CARATULA"

COLOR_UNIDAD = 3      # verde -> Unidad funcional / complementaria (capa DOMINIO)
COLOR_COMUN = 2       # amarillo -> Común (capa DOMINIO)
COLOR_CUBIERTA = 1    # rojo -> cubierta, excluida del dimensionado de SUPERFICIE
COLOR_MURO_COL = 7    # blanco -> muro común exterior / columnas ("tubo y col"):
                       # se dimensiona aparte, con regla espejada (ver más abajo)
COLOR_BYLAYER = 256
COLOR_APOYO = 6        # magenta -> polígono de apoyo para completar planillas de
                        # superficies a mano; NUNCA se dimensiona

# color 7 (COLOR_MURO_COL) se excluye del recorrido NORMAL de SUPERFICIE porque
# se procesa aparte, con la regla especial de "tubo y col" (ver más abajo).
SUP_EXCLUDE_COLORS = (COLOR_CUBIERTA, COLOR_MURO_COL, COLOR_BYLAYER, COLOR_APOYO)

# Offset "ideal" único (metros reales) para DOMINIO, SUPERFICIE y tubo/col: las
# tres capas quedan a la misma distancia típica del borde del polígono.
OFFSET = 0.25
TOL_NORMAL = 0.001      # metros, tolerancia para el test punto-dentro-de-polígono
EDGE_KEY_PREC = 3       # mm de precisión para detectar coincidencia de aristas DOMINIO vs SUPERFICIE
DECIMALES = 2            # cantidad de decimales del texto de cota (siempre con punto)

# Candidatos de offset (multiplicadores sobre el offset "ideal") que se prueban,
# en orden de preferencia, para separar cotas que colisionarían entre sí sin
# dejar de caer 100% dentro del polígono.
OFFSET_MULT_CANDIDATES = [1.0, 0.7, 1.4, 0.5, 1.8, 0.35, 2.3, 0.22, 0.15]
MIN_OFFSET = 0.03

# Tamaños de texto progresivamente más chicos que se prueban cuando ni variando
# el offset ni corriendo tangencialmente alcanza para que el rótulo entre
# completo (sin cortar el polígono ni solaparse con otra cota).
BOX_SCALE_CANDIDATES = [1.0, 0.85, 0.7, 0.55, 0.42]


# ---------------------------------------------------------------------------
# Geometría de polígonos y aristas
# ---------------------------------------------------------------------------

def get_edges(poly_entity):
    pts = [(v[0], v[1]) for v in poly_entity.get_points()]
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    return pts, edges


def calc_normales(p1, p2):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return (0.0, 0.0), (0.0, 0.0)
    tx, ty = dx / L, dy / L
    nx, ny = -ty, tx             # normal1: rotación +90° (izquierda)
    return (nx, ny), (-nx, -ny)  # normal1, normal2


def shoelace_signed_area2(pts):
    n = len(pts)
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s


def elegir_normal_interior(shp_poly, p1, p2, normal1, normal2, pts_poly):
    """Determina cuál de las dos normales de una arista apunta hacia el
    INTERIOR real del polígono, con un test punto-en-polígono (shapely) en vez
    de asumir el sentido de giro global: así funciona igual en polígonos
    cóncavos, con entrantes o casi auto-tangentes. El sentido de giro
    (shoelace) se usa solo como respaldo en casos degenerados/de borde."""
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    c1 = Point(mx + normal1[0] * TOL_NORMAL, my + normal1[1] * TOL_NORMAL)
    c2 = Point(mx + normal2[0] * TOL_NORMAL, my + normal2[1] * TOL_NORMAL)
    if shp_poly.contains(c1):
        return 1, normal1
    if shp_poly.contains(c2):
        return 2, normal2
    ccw = shoelace_signed_area2(pts_poly) > 0
    return (1, normal1) if ccw else (2, normal2)


def clasificar_arriba_abajo(vx, vy, normal):
    """Decide si el estilo de cota debe ser '...-Arriba' (sobrelínea, \\O<>) o
    '...-Abajo' (subrayado, \\L<>) para que el texto se lea de forma natural
    (sin que haya que girar la cabeza) desde la posición de un lector del
    plano. Se normaliza la dirección de la arista a una "dirección de
    lectura" (componente X >= 0, o Y >= 0 si es vertical) y se compara por
    producto cruzado contra la normal interior: si la normal queda a la
    izquierda de esa dirección -> Arriba; a la derecha -> Abajo. Devuelve
    también el ángulo de lectura (radianes, rango (-90°, 90°]), que además
    se usa para orientar el rectángulo de colisión del texto y la rotación
    real del MTEXT (ver más abajo)."""
    if vx > 1e-9 or (abs(vx) <= 1e-9 and vy > 0):
        rx, ry = vx, vy
    else:
        rx, ry = -vx, -vy
    cross = rx * normal[1] - ry * normal[0]
    return cross > 0, math.atan2(ry, rx)  # (True = Arriba), ángulo de lectura (rad)


def reading_angle(vx, vy):
    """Mismo ángulo de lectura normalizado que clasificar_arriba_abajo, para
    aristas (SUPERFICIE) que no necesitan booleano Arriba/Abajo."""
    if vx > 1e-9 or (abs(vx) <= 1e-9 and vy > 0):
        rx, ry = vx, vy
    else:
        rx, ry = -vx, -vy
    return math.atan2(ry, rx)


def edge_key(p1, p2, prec=EDGE_KEY_PREC):
    a = (round(p1[0], prec), round(p1[1], prec))
    b = (round(p2[0], prec), round(p2[1], prec))
    return frozenset((a, b))


# ---------------------------------------------------------------------------
# Colocación del texto: rectángulo orientado (OBB) + búsqueda sin colisión
# ---------------------------------------------------------------------------

def text_rect(center, angle, dimtxt, n_chars):
    """Rectángulo orientado (shapely Polygon) que aproxima el área real que va
    a ocupar el rótulo de texto, en las mismas unidades que `center`."""
    half_w = 0.5 * 0.55 * dimtxt * max(n_chars, 1) + 0.15 * dimtxt
    half_h = 0.52 * dimtxt
    ca, sa = math.cos(angle), math.sin(angle)
    corners = []
    for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        dx, dy = sx * half_w, sy * half_h
        rx = dx * ca - dy * sa
        ry = dx * sa + dy * ca
        corners.append((center[0] + rx, center[1] + ry))
    return Polygon(corners)


def elegir_offset_sin_colision(shp_poly, p1, p2, mx, my, normal, tangent, length,
                                offset_ideal, angle, dimtxt, n_chars, placed_rects):
    """Busca la mejor posición para el texto de una cota (DOMINIO o
    SUPERFICIE) sobre el lado INTERIOR del polígono: prueba una grilla de
    distancias (múltiplos del offset ideal) combinadas con pequeños
    corrimientos a lo largo de la propia arista (para poder ubicar dos cotas
    "una al lado de la otra" en vez de superpuestas), y de las posiciones que
    dejan el rectángulo de texto TOTALMENTE contenido en el polígono elige la
    primera que además no se superponga con ningún rectángulo ya colocado en
    el piso. Si ninguna combinación logra ambas condiciones, reduce
    progresivamente el tamaño del texto y repite la búsqueda completa. Solo
    como último recurso (arista más angosta que el texto aún reducido al
    mínimo) se cae a garantizar únicamente que el CENTRO del texto quede
    dentro del polígono."""
    tang_fracs = [0.0, 0.15, -0.15, 0.28, -0.28, 0.40, -0.40, 0.52, -0.52]
    max_tang = min(length * 0.45, 0.7)

    for box_scale in BOX_SCALE_CANDIDATES:
        dtxt = dimtxt * box_scale
        contenidos = []
        for mult in OFFSET_MULT_CANDIDATES:
            off = offset_ideal * mult
            if off < MIN_OFFSET:
                continue
            for tf in tang_fracs:
                tshift = tf * max_tang
                center = (
                    mx + normal[0] * off + tangent[0] * tshift,
                    my + normal[1] * off + tangent[1] * tshift,
                )
                rect = text_rect(center, angle, dtxt, n_chars)
                if shp_poly.contains(rect):
                    contenidos.append((off, center, rect))

        for off, center, rect in contenidos:
            if not any(rect.intersects(p) for p in placed_rects):
                tag = "ok" if box_scale == 1.0 else "ok_texto_reducido"
                return off, center, rect, tag, box_scale

        if contenidos:
            best = min(contenidos, key=lambda t: sum(t[2].intersection(p).area for p in placed_rects))
            tag = "contained_overlap" if box_scale == 1.0 else "contained_overlap_texto_reducido"
            return best[0], best[1], best[2], tag, box_scale

    # Ni reduciendo el texto se logró contenerlo 100%: se reduce el offset
    # hasta que al menos el CENTRO del texto quede adentro (último recurso).
    min_scale = BOX_SCALE_CANDIDATES[-1]
    off = offset_ideal
    while off > 0.005:
        center = (mx + normal[0] * off, my + normal[1] * off)
        if shp_poly.contains(Point(center)):
            rect = text_rect(center, angle, dimtxt * min_scale, n_chars)
            return off, center, rect, "fallback_center_only", min_scale
        off *= 0.6
    center = (mx + normal[0] * TOL_NORMAL, my + normal[1] * TOL_NORMAL)
    rect = text_rect(center, angle, dimtxt * min_scale, n_chars)
    return TOL_NORMAL, center, rect, "fallback_tol_only", min_scale


def elegir_offset_exterior_sin_colision(shp_poly, mx, my, ext_normal, tangent, length,
                                         offset_ideal, angle, dimtxt, n_chars, placed_rects):
    """Variante de elegir_offset_sin_colision para 'tubo y col': a diferencia
    de DOMINIO/SUPERFICIE, aquí el texto va del lado EXTERIOR del polígono
    (columnas y tramos de muro común son formas demasiado chicas para que el
    texto entre adentro; replica el criterio del código fuente original de la
    oficina: usa la normal opuesta a la interior). El criterio de "contenido"
    se invierte: en vez de exigir que el rectángulo quede DENTRO del
    polígono, exige que NO lo invada."""
    tang_fracs = [0.0, 0.15, -0.15, 0.28, -0.28, 0.40, -0.40, 0.52, -0.52]
    max_tang = min(length * 0.45, 0.7)

    for box_scale in BOX_SCALE_CANDIDATES:
        dtxt = dimtxt * box_scale
        libres = []
        for mult in OFFSET_MULT_CANDIDATES:
            off = offset_ideal * mult
            if off < MIN_OFFSET:
                continue
            for tf in tang_fracs:
                tshift = tf * max_tang
                center = (
                    mx + ext_normal[0] * off + tangent[0] * tshift,
                    my + ext_normal[1] * off + tangent[1] * tshift,
                )
                rect = text_rect(center, angle, dtxt, n_chars)
                if not rect.intersects(shp_poly):
                    libres.append((off, center, rect))

        for off, center, rect in libres:
            if not any(rect.intersects(p) for p in placed_rects):
                tag = "ok_ext" if box_scale == 1.0 else "ok_ext_texto_reducido"
                return off, center, rect, tag, box_scale

        if libres:
            best = min(libres, key=lambda t: sum(t[2].intersection(p).area for p in placed_rects))
            tag = "ext_overlap" if box_scale == 1.0 else "ext_overlap_texto_reducido"
            return best[0], best[1], best[2], tag, box_scale

    # último recurso: offset ideal sin verificar colisión (en la práctica casi
    # no ocurre, porque alejarse por la normal exterior casi siempre libera
    # espacio)
    min_scale = BOX_SCALE_CANDIDATES[-1]
    off = offset_ideal
    center = (mx + ext_normal[0] * off, my + ext_normal[1] * off)
    rect = text_rect(center, angle, dimtxt * min_scale, n_chars)
    return off, center, rect, "fallback_ext_no_check", min_scale


# ---------------------------------------------------------------------------
# Generación de la entidad DIMENSION
# ---------------------------------------------------------------------------

def strip_arrow_blocks(dimension_entity, doc):
    """ezdxf inserta bloques de flecha (p. ej. _CLOSEDFILLED) a escala 1.0 en
    el bloque de geometría de una dimensión alineada, incluso cuando el
    estilo tiene DIMASZ=0 (sin flecha). A la escala de papel de un plano MH
    (~1:100) eso se ve como un triángulo gigante tapando el dibujo. Las
    dimensiones nativas de AutoCAD con estos mismos estilos (sin flecha, sin
    línea) solo tienen MTEXT + POINT en su bloque de geometría — nunca
    INSERT — así que se eliminan a mano después de renderizar."""
    block = doc.blocks.get(dimension_entity.dxf.geometry)
    for e in list(block):
        if e.dxftype() == "INSERT":
            block.delete_entity(e)


def set_dim_content(dimension_entity, doc, text_paper_point, text_string, char_height_paper,
                     rotation_deg=None):
    """Sobrescribe a mano el contenido real del MTEXT dentro del bloque de
    geometría de la dimensión (posición, texto, tamaño y rotación), en vez de
    confiar en que ezdxf calcule estos valores correctamente a partir de los
    parámetros de entrada de add_aligned_dim — ver notas de cada parámetro
    más abajo y en docs/REGLA-ACOTAMIENTO.md."""
    block = doc.blocks.get(dimension_entity.dxf.geometry)
    for e in block:
        if e.dxftype() == "MTEXT":
            e.dxf.insert = text_paper_point
            e.text = text_string
            if char_height_paper is not None:
                e.dxf.char_height = char_height_paper
            if rotation_deg is not None:
                # ezdxf orienta el MTEXT renderizado según el ángulo crudo del
                # segmento p1->p2 (el mismo que queda en
                # dimension_entity.dxf.angle), que depende del orden en que
                # la polilínea original define sus vértices y puede caer en
                # cualquiera de los 360°, incluida la mitad del círculo que
                # queda "boca abajo" para un lector humano. Se sobreescribe
                # con el ángulo de LECTURA normalizado (rango (-90°, 90°])
                # calculado en clasificar_arriba_abajo/reading_angle, que ya
                # se usa para elegir Arriba/Abajo y orientar el rectángulo de
                # colisión del texto.
                e.dxf.rotation = rotation_deg
    dimension_entity.dxf.text_midpoint = text_paper_point
    dimension_entity.dxf.defpoint = text_paper_point


def make_dim(paper, doc, p1_model, p2_model, to_paper, text_model,
             dimlfac, style_name, text_core, wrap, box_scale=1.0, dimtxt_paper=None,
             rotation_deg=None, dimpost=None):
    length = math.hypot(p2_model[0] - p1_model[0], p2_model[1] - p1_model[1])
    if length < 1e-6:
        return None, length
    paper_p1 = to_paper(p1_model)
    paper_p2 = to_paper(p2_model)
    text_paper = to_paper(text_model)

    override = {"dimlfac": dimlfac, "dimdsep": 46}  # 46 = '.' (fuerza separador decimal punto)
    if dimpost is not None:
        # Además de dejar el prefijo \L/\O ya escrito dentro del propio texto
        # (lo que ya alcanza para que se vea bien en el dibujo), se
        # sobreescribe también el DIMPOST a nivel de esta dimensión puntual
        # (mismo mecanismo de "override" que dimlfac/dimdsep, vía XDATA
        # "DSTYLE"), para que el valor EFECTIVO de esta cota en particular
        # también se refleje en el panel de Propiedades de AutoCAD.
        override["dimpost"] = dimpost

    # NOTA sobre `distance`: se pasa siempre 0.0 porque el parámetro
    # `distance` de add_aligned_dim resultó poco confiable a las escalas de
    # papel muy chicas usadas en estos planos (en pruebas puntuales, llegó a
    # colocar el texto del lado y a la distancia equivocados). La posición
    # real del texto se fija a mano después de renderizar, con
    # set_dim_content (ver más abajo).
    dimo = paper.add_aligned_dim(
        p1=paper_p1,
        p2=paper_p2,
        distance=0.0,
        dimstyle=style_name,
        override=override,
        dxfattribs={"layer": LAYER_CARATULA},
    )
    dimo.render()
    d = dimo.dimension
    d.dxf.actual_measurement = length
    strip_arrow_blocks(d, doc)

    text_string = wrap(text_core) if wrap else text_core
    char_height_paper = dimtxt_paper * box_scale if dimtxt_paper is not None else None
    set_dim_content(d, doc, text_paper, text_string, char_height_paper, rotation_deg=rotation_deg)
    return d, length


# ---------------------------------------------------------------------------
# Parche de DIMPOST a nivel de tabla DIMSTYLE (post-proceso de texto)
# ---------------------------------------------------------------------------

DIMPOST_POR_ESTILO = {
    "MH-Unidad-Abajo": "\\L<>",
    "MH-Unidad-Arriba": "\\O<>",
    "MH-Común-Abajo": "\\L<>",
    "MH-Común-Arriba": "\\O<>",
}


def patch_dimpost(path, dimpost_by_style):
    """ezdxf 1.4.4 no exporta el atributo DIMPOST (código de grupo 3) de la
    tabla DIMSTYLE para DXF R2007 en adelante (ausente de su propia tabla de
    exportación EXPORT_MAP_R2007 en ezdxf/entities/dimstyle.py, a diferencia
    de EXPORT_MAP_R2000/EXPORT_MAP_R12, que sí lo incluyen) — asignar
    `dimstyle.dxf.dimpost = ...` antes de guardar funciona en memoria pero se
    pierde al guardar y releer el archivo. El código de grupo 3 sigue siendo
    válido en la especificación DXF para cualquier versión, así que se
    agrega a mano como post-proceso de texto sobre el DXF ya guardado:
    ubica cada tabla DIMSTYLE por su marcador de subclase
    ("AcDbDimStyleTableRecord") y nombre (código 2), e inserta el tag
    "3 / valor" justo después del tag de flags (código 70) — mismo orden que
    usa ezdxf para R12/R2000, donde sí exporta dimpost."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    out = []
    i = 0
    n = len(lines)
    patched = []
    while i < n:
        out.append(lines[i])
        if lines[i].strip() == "100" and i + 1 < n and lines[i + 1].strip() == "AcDbDimStyleTableRecord":
            out.append(lines[i + 1])
            i += 2
            if i + 1 < n and lines[i].strip() == "2":
                name = lines[i + 1].strip()
                out.append(lines[i])
                out.append(lines[i + 1])
                i += 2
                if i + 1 < n and lines[i].strip() == "70":
                    out.append(lines[i])
                    out.append(lines[i + 1])
                    i += 2
                    if name in dimpost_by_style:
                        out.append(" 3\n")
                        out.append(dimpost_by_style[name] + "\n")
                        patched.append(name)
            continue
        i += 1

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return patched


# ---------------------------------------------------------------------------
# Versión del archivo de entrada
# ---------------------------------------------------------------------------

# Solo se aceptan archivos DXF guardados como "AutoCAD 2010/LT2010 DXF"
# (código interno AC1024). Desde la versión 2007 los DXF se guardan siempre en
# UTF-8, así que exigir 2010 elimina además los problemas de codificación de
# archivos viejos (cp1252 y similares). También es la versión con la que se
# validó la lógica de este script contra planos reales.
VERSION_REQUERIDA = "AC1024"
NOMBRES_VERSION = {
    "AC1009": "R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "2000",
    "AC1018": "2004", "AC1021": "2007", "AC1024": "2010", "AC1027": "2013",
    "AC1032": "2018",
}


class VersionDXFError(ValueError):
    """El archivo no es un DXF 2010 (AC1024)."""


def nombre_version(acadver):
    return NOMBRES_VERSION.get(acadver, acadver or "desconocida")


def leer_version_dxf(path):
    """Lee la versión ($ACADVER) del encabezado del DXF sin cargar el archivo
    completo. Devuelve None si no la encuentra (archivo dañado o no DXF)."""
    with open(path, "rb") as f:
        cabecera = f.read(256 * 1024)
    if cabecera.startswith(b"AutoCAD Binary DXF"):
        return "BINARIO"
    texto = cabecera.decode("latin-1")
    lineas = [l.strip() for l in texto.splitlines()]
    for i, l in enumerate(lineas):
        if l == "$ACADVER" and i + 2 < len(lineas):
            return lineas[i + 2]
    return None


def verificar_version(path):
    """Lanza VersionDXFError si el archivo no es un DXF 2010 en formato texto."""
    v = leer_version_dxf(path)
    if v == "BINARIO":
        raise VersionDXFError(
            "El archivo es un DXF binario. Guardalo como DXF de texto: en AutoCAD, "
            "Guardar como > «AutoCAD 2010/LT2010 DXF (*.dxf)», sin la opción binaria.")
    if v is None:
        raise VersionDXFError(
            "No se pudo leer la versión del archivo: no parece ser un DXF válido.")
    if v != VERSION_REQUERIDA:
        raise VersionDXFError(
            f"El archivo es un DXF versión {nombre_version(v)} ({v}). Se requiere DXF 2010 "
            f"(AC1024): en AutoCAD, Guardar como > «AutoCAD 2010/LT2010 DXF (*.dxf)».")
    return v


# ---------------------------------------------------------------------------
# Análisis previo del archivo (pisos y layouts disponibles)
# ---------------------------------------------------------------------------

ESTILOS_REQUERIDOS = list(DIMPOST_POR_ESTILO) + ["Lado-Polígono-Superficie"]
ESTADOS_A_REVISAR = (
    "fallback_center_only", "fallback_tol_only", "fallback_ext_no_check",
    "contained_overlap", "contained_overlap_texto_reducido",
    "ext_overlap", "ext_overlap_texto_reducido",
)


def _viewports(paper):
    return [e for e in paper if e.dxftype() == "VIEWPORT" and e.dxf.status >= 2]


def _vp_muestra_piso(vp, floor):
    """El viewport de un piso es el que NO tiene congelada su capa DOMINIO
    (comparación sin distinguir mayúsculas, como hace AutoCAD)."""
    dlayer = f"M-MH-{floor}-DOMINIO".upper()
    return dlayer not in {l.upper() for l in vp.frozen_layers}


def analizar(path):
    """Inspecciona el DXF y devuelve qué pisos y layouts tiene, para que el
    usuario no tenga que tipearlos. Lanza VersionDXFError si no es DXF 2010."""
    version = verificar_version(path)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    pisos = []
    for layer in doc.layers:
        nombre = layer.dxf.name
        if nombre.upper().startswith("M-MH-") and nombre.upper().endswith("-DOMINIO"):
            pisos.append(nombre[5:-8])

    polilineas = list(msp.query("LWPOLYLINE"))
    conteo = {}
    for p in pisos:
        dlayer, slayer = f"M-MH-{p}-DOMINIO", f"M-MH-{p}-SUP"
        conteo[p] = {
            "dominio": sum(1 for e in polilineas if e.dxf.layer == dlayer),
            "sup": sum(1 for e in polilineas if e.dxf.layer == slayer),
        }

    layouts = []
    for nombre in doc.layout_names_in_taborder():
        if nombre.lower() == "model":
            continue
        vps = _viewports(doc.layout(nombre))
        por_piso = {p: sum(1 for vp in vps if _vp_muestra_piso(vp, p)) for p in pisos}
        layouts.append({
            "nombre": nombre,
            "viewports": len(vps),
            "pisos_ok": [p for p, n in por_piso.items() if n == 1],
            "por_piso": por_piso,
        })

    # Layout sugerido: FORMATO-A1 si existe; si no, el que más pisos resuelve.
    sugerido = None
    if any(l["nombre"] == "FORMATO-A1" for l in layouts):
        sugerido = "FORMATO-A1"
    elif layouts:
        sugerido = max(layouts, key=lambda l: len(l["pisos_ok"]))["nombre"]

    existentes = {s.dxf.name for s in doc.dimstyles}
    faltantes = [s for s in ESTILOS_REQUERIDOS if s not in existentes]

    return {
        "version": version,
        "version_nombre": nombre_version(version),
        "pisos": pisos,
        "conteo": conteo,
        "layouts": layouts,
        "layout_sugerido": sugerido,
        "estilos_faltantes": faltantes,
        "capa_caratula": LAYER_CARATULA in doc.layers,
    }


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def acotar(src, dst, floors, layout_name, verbose=True, log=None):
    """Acota el DXF `src` y guarda el resultado en `dst`.

    Devuelve un resumen (dict) con las cotas generadas por estilo, el detalle
    de colocación, los avisos y la lista de cotas a revisar a mano. `log` es
    una función que recibe cada línea de texto del informe (por defecto,
    print cuando verbose=True)."""
    if log is None:
        log = print if verbose else (lambda *a, **k: None)

    verificar_version(src)
    doc = ezdxf.readfile(src)
    msp = doc.modelspace()
    try:
        paper = doc.layout(layout_name)
    except KeyError:
        raise ValueError(f"El archivo no tiene un layout llamado «{layout_name}».")
    existentes = {s.dxf.name for s in doc.dimstyles}
    faltantes = [s for s in ESTILOS_REQUERIDOS if s not in existentes]
    if faltantes:
        raise ValueError("Faltan estilos de cota de la plantilla CABA: " + ", ".join(faltantes))

    avisos = []

    def avisar(texto):
        avisos.append(texto)
        log(f"ATENCION {texto}")

    # Cada layout trae un VIEWPORT por piso; se identifica por qué capa
    # M-MH-<piso>-DOMINIO NO está congelada en ese viewport.
    viewports = _viewports(paper)
    floor_vp = {}
    for floor in floors:
        candidates = [vp for vp in viewports if _vp_muestra_piso(vp, floor)]
        if len(candidates) != 1:
            avisar(f"piso {floor}: {len(candidates)} viewports candidatos en «{layout_name}» "
                   f"(se esperaba 1)" + ("; se usa el primero" if candidates else "; se omite el piso"))
        floor_vp[floor] = candidates[0] if candidates else None

    stats = Counter()
    skipped_degenerate = 0
    skipped_dup_sup = 0
    status_counts = Counter()
    revisar = []  # cotas cuya colocación conviene revisar a mano

    def registrar(floor, capa, style_name, length, resolved, text_paper, handle):
        status_counts[resolved] += 1
        if resolved in ESTADOS_A_REVISAR:
            revisar.append({
                "piso": floor, "capa": capa, "estilo": style_name,
                "medida": f"{length:.{DECIMALES}f}", "estado": resolved,
                "polilinea": handle, "x": text_paper[0], "y": text_paper[1],
            })

    for floor in floors:
        vp = floor_vp[floor]
        if vp is None:
            continue

        # scale: unidades de papel por unidad de modelo. dimlfac es su
        # inverso, para que el texto de la cota muestre la medida real del
        # terreno aunque los puntos de la dimensión estén en espacio papel.
        scale = vp.dxf.height / vp.dxf.view_height
        dimlfac = vp.dxf.view_height / vp.dxf.height
        vcx, vcy = vp.dxf.view_center_point.x, vp.dxf.view_center_point.y
        pcx, pcy = vp.dxf.center.x, vp.dxf.center.y

        def to_paper(pt, pcx=pcx, pcy=pcy, vcx=vcx, vcy=vcy, scale=scale):
            return (pcx + (pt[0] - vcx) * scale, pcy + (pt[1] - vcy) * scale)

        placed_rects = []  # rectángulos de texto ya ubicados en este piso (espacio modelo)

        # ---------------- DOMINIO ----------------
        dlayer = f"M-MH-{floor}-DOMINIO"
        dom_polys = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == dlayer]
        dom_edge_keys = set()

        for poly in dom_polys:
            color = poly.dxf.color
            if color == COLOR_UNIDAD:
                base_style = "MH-Unidad"
                dimtxt = 0.002
            elif color == COLOR_COMUN:
                base_style = "MH-Común"
                dimtxt = 0.0018
            else:
                avisar(f"DOMINIO {floor} polilínea {poly.dxf.handle}: color inesperado {color}, "
                       f"se acota como Unidad")
                base_style = "MH-Unidad"
                dimtxt = 0.002
            dimtxt_model = dimtxt / scale  # dimtxt está en espacio papel; a espacio modelo (real)

            pts, edges = get_edges(poly)
            shp = Polygon(pts)
            if not shp.is_valid:
                shp = shp.buffer(0)

            for p1, p2 in edges:
                dom_edge_keys.add(edge_key(p1, p2))
                vx, vy = p2[0] - p1[0], p2[1] - p1[1]
                length = math.hypot(vx, vy)
                if length < 1e-6:
                    skipped_degenerate += 1
                    continue
                n1, n2 = calc_normales(p1, p2)
                which, normal = elegir_normal_interior(shp, p1, p2, n1, n2, pts)
                arriba, angle = clasificar_arriba_abajo(vx, vy, normal)
                style_name = f"{base_style}-{'Arriba' if arriba else 'Abajo'}"
                tangent = (vx / length, vy / length)

                core = f"{length:.{DECIMALES}f}"
                n_chars = len(core)
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                offset_real, text_model, rect, resolved, box_scale = elegir_offset_sin_colision(
                    shp, p1, p2, mx, my, normal, tangent, length, OFFSET, angle,
                    dimtxt_model, n_chars, placed_rects)
                registrar(floor, dlayer, style_name, length, resolved,
                          to_paper(text_model), poly.dxf.handle)
                placed_rects.append(rect)

                wrap = (lambda s: f"\\O{s}") if arriba else (lambda s: f"\\L{s}")
                dimpost = "\\O<>" if arriba else "\\L<>"
                d, _ = make_dim(paper, doc, p1, p2, to_paper, text_model,
                                 dimlfac, style_name, core, wrap,
                                 box_scale=box_scale, dimtxt_paper=dimtxt,
                                 rotation_deg=math.degrees(angle), dimpost=dimpost)
                if d is not None:
                    stats[style_name] += 1

        # ---------------- SUPERFICIE ----------------
        slayer = f"M-MH-{floor}-SUP"
        sup_polys = [
            e for e in msp.query("LWPOLYLINE")
            if e.dxf.layer == slayer and e.dxf.color not in SUP_EXCLUDE_COLORS
        ]
        dimtxt_sup_model = 0.0018 / scale

        for poly in sup_polys:
            pts, edges = get_edges(poly)
            shp = Polygon(pts)
            if not shp.is_valid:
                shp = shp.buffer(0)

            for p1, p2 in edges:
                if edge_key(p1, p2) in dom_edge_keys:
                    skipped_dup_sup += 1
                    continue
                vx, vy = p2[0] - p1[0], p2[1] - p1[1]
                length = math.hypot(vx, vy)
                if length < 1e-6:
                    skipped_degenerate += 1
                    continue
                n1, n2 = calc_normales(p1, p2)
                which, normal = elegir_normal_interior(shp, p1, p2, n1, n2, pts)
                angle = reading_angle(vx, vy)
                tangent = (vx / length, vy / length)

                core = f"{length:.{DECIMALES}f}"
                n_chars = len(core)
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                offset_real, text_model, rect, resolved, box_scale = elegir_offset_sin_colision(
                    shp, p1, p2, mx, my, normal, tangent, length, OFFSET, angle,
                    dimtxt_sup_model, n_chars, placed_rects)
                registrar(floor, slayer, "Lado-Polígono-Superficie", length, resolved,
                          to_paper(text_model), poly.dxf.handle)
                placed_rects.append(rect)

                d, _ = make_dim(paper, doc, p1, p2, to_paper, text_model,
                                 dimlfac, "Lado-Polígono-Superficie", core, None,
                                 box_scale=box_scale, dimtxt_paper=0.0018,
                                 rotation_deg=math.degrees(angle))
                if d is not None:
                    stats["Lado-Polígono-Superficie"] += 1

        # ---------------- TUBO Y COL ----------------
        # Polígonos color 7 (blanco) dentro de la capa SUP: columnas / muro
        # común exterior. No entran en el recorrido normal de SUPERFICIE
        # (están en SUP_EXCLUDE_COLORS); se dimensionan aparte con la regla
        # espejada: mismo criterio Arriba/Abajo que "Común" pero INVERTIDO,
        # reutilizando los estilos MH-Común-Abajo/Arriba, y el texto se ubica
        # del lado EXTERIOR del polígono (no interior) porque suelen ser
        # formas demasiado chicas (columnas) para que el texto entre adentro.
        tubo_col_polys = [
            e for e in msp.query("LWPOLYLINE")
            if e.dxf.layer == slayer and e.dxf.color == COLOR_MURO_COL
        ]
        dimtxt_tubo_model = 0.0018 / scale

        for poly in tubo_col_polys:
            pts, edges = get_edges(poly)
            shp = Polygon(pts)
            if not shp.is_valid:
                shp = shp.buffer(0)

            for p1, p2 in edges:
                if edge_key(p1, p2) in dom_edge_keys:
                    skipped_dup_sup += 1
                    continue
                vx, vy = p2[0] - p1[0], p2[1] - p1[1]
                length = math.hypot(vx, vy)
                if length < 1e-6:
                    skipped_degenerate += 1
                    continue
                n1, n2 = calc_normales(p1, p2)
                which, normal_int = elegir_normal_interior(shp, p1, p2, n1, n2, pts)
                normal_ext = n2 if which == 1 else n1
                arriba_comun, angle = clasificar_arriba_abajo(vx, vy, normal_int)
                arriba_tubo = not arriba_comun  # espejado respecto de "Común"
                style_name = f"MH-Común-{'Arriba' if arriba_tubo else 'Abajo'}"
                tangent = (vx / length, vy / length)

                core = f"{length:.{DECIMALES}f}"
                n_chars = len(core)
                mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
                offset_real, text_model, rect, resolved, box_scale = elegir_offset_exterior_sin_colision(
                    shp, mx, my, normal_ext, tangent, length, OFFSET, angle,
                    dimtxt_tubo_model, n_chars, placed_rects)
                registrar(floor, slayer, f"{style_name} (tubo/col)", length, resolved,
                          to_paper(text_model), poly.dxf.handle)
                placed_rects.append(rect)

                wrap = (lambda s: f"\\O{s}") if arriba_tubo else (lambda s: f"\\L{s}")
                dimpost = "\\O<>" if arriba_tubo else "\\L<>"
                d, _ = make_dim(paper, doc, p1, p2, to_paper, text_model,
                                 dimlfac, style_name, core, wrap,
                                 box_scale=box_scale, dimtxt_paper=0.0018,
                                 rotation_deg=math.degrees(angle), dimpost=dimpost)
                if d is not None:
                    stats[f"{style_name} (tubo/col)"] += 1

    log("")
    log("Resumen de cotas generadas:")
    for k, v in sorted(stats.items()):
        log(f"  {k}: {v}")
    log(f"  TOTAL: {sum(stats.values())}")
    log(f"  Aristas SUPERFICIE omitidas por coincidir con DOMINIO: {skipped_dup_sup}")
    log(f"  Aristas degeneradas omitidas: {skipped_degenerate}")
    log(f"  Detalle de colocación: {dict(status_counts)}")
    if revisar:
        log(f"  Cotas a revisar a mano: {len(revisar)}")

    auditor = doc.audit()
    errores_auditoria = [str(err) for err in auditor.errors]
    if errores_auditoria:
        log(f"ERRORES DE AUDITORIA: {len(errores_auditoria)}")
        for err in errores_auditoria[:20]:
            log(f"   {err}")
    else:
        log("Auditoría OK, sin errores.")

    doc.saveas(dst)
    log(f"Guardado: {dst}")

    patched = patch_dimpost(dst, DIMPOST_POR_ESTILO)
    log(f"DIMPOST parcheado en {len(patched)} estilos: {patched}")

    return dict(
        stats=dict(stats), total=sum(stats.values()),
        status_counts=dict(status_counts),
        skipped_dup_sup=skipped_dup_sup, skipped_degenerate=skipped_degenerate,
        avisos=avisos, revisar=revisar,
        errores_auditoria=errores_auditoria, dimpost_parcheado=patched,
        pisos=[f for f in floors if floor_vp.get(f) is not None], layout=layout_name,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Acotamiento automático de polígonos DOMINIO/SUPERFICIE en planos MH (CABA). "
                    "El archivo de entrada debe ser DXF 2010 (AC1024).")
    parser.add_argument("entrada", help="DXF 2010 de entrada, sin acotar")
    parser.add_argument("salida", help="DXF de salida, con las cotas agregadas")
    parser.add_argument("--pisos", nargs="+", default=None,
                         help="Lista de pisos a procesar (deben existir las capas "
                              "M-MH-<piso>-DOMINIO/SUP). Por defecto se detectan del archivo.")
    parser.add_argument("--layout", default=None,
                         help="Nombre del layout (paper space) donde están los VIEWPORT por piso. "
                              "Por defecto FORMATO-A1, o el que mejor coincida.")
    args = parser.parse_args()

    try:
        info = analizar(args.entrada)
        pisos = args.pisos or info["pisos"]
        layout = args.layout or info["layout_sugerido"]
        if not pisos:
            sys.exit("No se encontraron capas M-MH-<piso>-DOMINIO en el archivo.")
        if not layout:
            sys.exit("El archivo no tiene layouts de papel.")
        print(f"DXF {info['version_nombre']} · layout {layout} · pisos {' '.join(pisos)}")
        if info["estilos_faltantes"]:
            print(f"ATENCION faltan estilos de cota: {', '.join(info['estilos_faltantes'])}")
        acotar(args.entrada, args.salida, pisos, layout)
    except (VersionDXFError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ezdxf.DXFError as exc:
        print(f"Error leyendo/escribiendo el DXF: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

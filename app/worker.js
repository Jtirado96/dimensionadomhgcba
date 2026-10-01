/*
 * worker.js — Worker de tipo módulo (Pyodide 314 no admite workers clásicos).
 * Corre Python (Pyodide) en un hilo aparte para que la página no
 * se congele mientras se acota el plano. Carga ezdxf + shapely y los módulos
 * acotar_mh.py y visor_svg.py del propio repositorio.
 *
 * Mensajes que recibe:   {id, tipo: "analizar", nombre, datos: ArrayBuffer}
 *                        {id, tipo: "acotar", pisos: [...], layout}
 *                        {id, tipo: "vista", fondo: "oscuro"|"claro"}
 * Mensajes que envía:    {tipo: "estado", texto}  ·  {tipo: "listo"}
 *                        {id, ok: true, ...}  ·  {id, ok: false, error, clase}
 */

const EZDXF_VERSION = "1.4.4"; // versión contra la que se validaron los parches
// Pyodide 314.0.7 (Python 3.14). Para actualizar, cambiar la versión en la URL.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

// Carpeta donde están acotar_mh.py y visor_svg.py (la raíz del sitio).
const BASE_MODULOS = self.location.href;
const ENTRADA = "/trabajo/entrada.dxf";
const SALIDA = "/trabajo/salida.dxf";

const FUENTE = "DejaVuSansCondensed.ttf";

const PEGAMENTO = `
import json, traceback

# Pyodide no trae fuentes del sistema: se registra la que viene en
# app/fuentes/ para que ezdxf pueda dibujar los textos de la vista previa.
from ezdxf.fonts import fonts as _fonts
_fonts.font_manager.clear()
_fonts.font_manager.build(["/fuentes"], support_dirs=False)

import acotar_mh, visor_svg

_ultimo = {"layout": None, "marcas": []}

def _error(e):
    clase = "version" if isinstance(e, acotar_mh.VersionDXFError) else type(e).__name__
    detalle = "" if isinstance(e, (ValueError,)) else traceback.format_exc()
    return json.dumps({"ok": False, "clase": clase, "error": str(e) or clase, "detalle": detalle})

def api_analizar(path):
    try:
        return json.dumps({"ok": True, "info": acotar_mh.analizar(path)})
    except Exception as e:
        return _error(e)

def api_acotar(src, dst, pisos, layout):
    lineas = []
    try:
        res = acotar_mh.acotar(src, dst, list(pisos), layout, log=lineas.append)
        marcas = [(r["x"], r["y"]) for r in res["revisar"]]
        _ultimo.update(layout=layout, marcas=marcas)
        svg = visor_svg.renderizar_layout(dst, layout, "oscuro", marcas)
        return json.dumps({"ok": True, "resumen": res, "informe": lineas, "svg": svg})
    except Exception as e:
        return _error(e)

def api_vista(dst, fondo):
    try:
        svg = visor_svg.renderizar_layout(dst, _ultimo["layout"], fondo, _ultimo["marcas"])
        return json.dumps({"ok": True, "svg": svg})
    except Exception as e:
        return _error(e)
`;

let py = null;

function estado(texto) {
  self.postMessage({ tipo: "estado", texto });
}

async function iniciar() {
  estado("Descargando Python para el navegador…");
  py = await loadPyodide();
  estado("Instalando shapely y numpy…");
  await py.loadPackage(["micropip", "numpy", "shapely", "pyparsing", "typing-extensions", "fonttools", "pillow"]);
  estado("Instalando ezdxf…");
  const micropip = py.pyimport("micropip");
  await micropip.install(`ezdxf==${EZDXF_VERSION}`);
  estado("Cargando el módulo de acotamiento…");
  py.FS.mkdirTree("/trabajo");
  py.FS.mkdirTree("/modulos");
  py.FS.mkdirTree("/fuentes");
  const fuente = await fetch(new URL(`fuentes/${FUENTE}`, BASE_MODULOS));
  if (!fuente.ok) throw new Error(`No se pudo descargar la fuente (${fuente.status})`);
  py.FS.writeFile(`/fuentes/${FUENTE}`, new Uint8Array(await fuente.arrayBuffer()));
  for (const nombre of ["acotar_mh.py", "visor_svg.py"]) {
    const url = new URL(`../${nombre}`, BASE_MODULOS);
    const resp = await fetch(url, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`No se pudo descargar ${nombre} (${resp.status})`);
    py.FS.writeFile(`/modulos/${nombre}`, await resp.text());
  }
  py.runPython(`import sys; sys.path.insert(0, "/modulos")`);
  py.runPython(PEGAMENTO);
}

const listo = iniciar().then(
  () => self.postMessage({ tipo: "listo" }),
  (err) => {
    self.postMessage({ tipo: "fallo", error: String(err && err.message ? err.message : err) });
    throw err;
  },
);

function llamar(funcion, ...args) {
  const f = py.globals.get(funcion);
  try {
    return JSON.parse(f(...args));
  } finally {
    f.destroy();
  }
}

self.onmessage = async (ev) => {
  const msg = ev.data;
  try {
    await listo;
  } catch (err) {
    self.postMessage({ id: msg.id, ok: false, error: "El motor de Python no pudo iniciarse." });
    return;
  }
  let res;
  try {
    if (msg.tipo === "analizar") {
      py.FS.writeFile(ENTRADA, new Uint8Array(msg.datos));
      res = llamar("api_analizar", ENTRADA);
    } else if (msg.tipo === "acotar") {
      estado("Acotando el plano…");
      res = llamar("api_acotar", ENTRADA, SALIDA, py.toPy(msg.pisos), msg.layout);
      if (res.ok) {
        const bytes = py.FS.readFile(SALIDA);
        res.archivo = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
      }
    } else if (msg.tipo === "vista") {
      res = llamar("api_vista", SALIDA, msg.fondo);
    } else {
      res = { ok: false, error: `Mensaje desconocido: ${msg.tipo}` };
    }
  } catch (err) {
    res = { ok: false, error: String(err && err.message ? err.message : err) };
  }
  res.id = msg.id;
  self.postMessage(res, res.archivo ? [res.archivo] : []);
};

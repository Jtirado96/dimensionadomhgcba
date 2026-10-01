/* Acotamiento MH — lógica de la interfaz.
 * El trabajo pesado (ezdxf + shapely) corre en app/worker.js con Pyodide. */
(() => {
  "use strict";

  const VERSION_REQUERIDA = "AC1024";
  const NOMBRES_VERSION = {
    AC1009: "R12", AC1012: "R13", AC1014: "R14", AC1015: "2000", AC1018: "2004",
    AC1021: "2007", AC1024: "2010", AC1027: "2013", AC1032: "2018",
  };
  const COLOR_MARCAS = "#ff2d55";
  const FONDOS = { oscuro: "#212830", claro: "#ffffff" };
  const ESTADOS = {
    fallback_center_only: "solo el centro del texto queda adentro",
    fallback_tol_only: "texto pegado al borde",
    fallback_ext_no_check: "texto exterior sin verificar",
    contained_overlap: "se superpone con otra cota",
    contained_overlap_texto_reducido: "texto reducido y superpuesto",
    ext_overlap: "se superpone con otra cota",
    ext_overlap_texto_reducido: "texto reducido y superpuesto",
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const el = {
    motor: $("#motor"), motorTexto: $("#motor-texto"),
    zona: $("#zona-carga"), input: $("#entrada-archivo"), errorCarga: $("#error-carga"),
    opciones: $("#panel-opciones"), nombre: $("#archivo-nombre"), version: $("#archivo-version"),
    selLayout: $("#sel-layout"), pisos: $("#lista-pisos"), avisosAnalisis: $("#avisos-analisis"),
    btnAcotar: $("#btn-acotar"),
    resultado: $("#panel-resultado"), visor: $("#visor"), visorCargando: $("#visor-cargando"),
    visorLayout: $("#visor-layout"),
    total: $("#cifra-total"), nPisos: $("#cifra-pisos"), dup: $("#cifra-dup"),
    nRevisar: $("#cifra-revisar"), cajaRevisar: $("#cifra-revisar-caja"),
    tablaEstilos: $("#tabla-estilos tbody"), listaRevisar: $("#lista-revisar"),
    revisarVacio: $("#revisar-vacio"), revisarIntro: $("#revisar-intro"),
    avisosResultado: $("#avisos-resultado"), informe: $("#informe"),
    descargaNombre: $("#descarga-nombre"), btnDescargar: $("#btn-descargar"),
  };

  const estado = { archivo: null, info: null, motorListo: false, svgs: {}, fondo: "oscuro", urlDescarga: null, ocupado: false };

  // ───────────── Motor (Web Worker con Pyodide) ─────────────
  const worker = new Worker("app/worker.js");
  const pendientes = new Map();
  let siguienteId = 1;

  function motor(estadoMotor, texto) {
    el.motor.dataset.estado = estadoMotor;
    el.motorTexto.textContent = texto;
  }

  worker.onmessage = (ev) => {
    const m = ev.data;
    if (m.tipo === "estado") {
      motor(estado.motorListo ? "listo" : "cargando", m.texto);
      return;
    }
    if (m.tipo === "listo") {
      estado.motorListo = true;
      motor("listo", "Motor listo · todo corre en tu navegador");
      return;
    }
    if (m.tipo === "fallo") {
      motor("fallo", "No se pudo cargar el motor de Python");
      mostrarError(`No se pudo cargar el motor de Python (${m.error}). Revisá la conexión a internet y recargá la página.`);
      return;
    }
    const p = pendientes.get(m.id);
    if (p) { pendientes.delete(m.id); p(m); }
  };
  worker.onerror = (ev) => {
    motor("fallo", "Error en el motor de Python");
    console.error(ev);
  };

  function pedir(mensaje, transferibles = []) {
    return new Promise((resolve) => {
      const id = siguienteId++;
      pendientes.set(id, resolve);
      worker.postMessage({ ...mensaje, id }, transferibles);
    });
  }

  // ───────────── Pasos ─────────────
  function paso(n) {
    $$(".pasos li").forEach((li) => {
      const k = Number(li.dataset.paso);
      li.classList.toggle("activo", k === n);
      li.classList.toggle("hecho", k < n);
    });
  }

  function mostrarError(texto, detalle) {
    el.errorCarga.innerHTML = esc(texto) + (detalle ? `<pre>${esc(detalle)}</pre>` : "");
    el.errorCarga.hidden = false;
  }

  function reiniciar() {
    estado.archivo = null; estado.info = null; estado.svgs = {};
    el.input.value = "";
    el.opciones.hidden = true;
    el.resultado.hidden = true;
    el.errorCarga.hidden = true;
    el.zona.hidden = false;
    el.zona.classList.remove("ocupada");
    if (estado.urlDescarga) { URL.revokeObjectURL(estado.urlDescarga); estado.urlDescarga = null; }
    paso(1);
  }

  // ───────────── Paso 1: carga y verificación de versión ─────────────
  function leerVersion(buffer) {
    const cabecera = new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 256 * 1024));
    const texto = new TextDecoder("latin1").decode(cabecera);
    if (texto.startsWith("AutoCAD Binary DXF")) return "BINARIO";
    const m = texto.match(/\$ACADVER\s*\r?\n\s*1\s*\r?\n\s*(\S+)/);
    return m ? m[1] : null;
  }

  async function cargarArchivo(file) {
    if (!file || estado.ocupado) return;
    el.errorCarga.hidden = true;
    if (!/\.dxf$/i.test(file.name)) {
      mostrarError(`«${file.name}» no es un archivo .dxf.`);
      return;
    }
    const buffer = await file.arrayBuffer();
    const v = leerVersion(buffer);
    if (v === "BINARIO") {
      mostrarError("El archivo es un DXF binario. Guardalo como DXF de texto: en AutoCAD, Guardar como › «AutoCAD 2010/LT2010 DXF (*.dxf)».");
      return;
    }
    if (!v) {
      mostrarError("No se pudo leer la versión del archivo: no parece ser un DXF válido.");
      return;
    }
    if (v !== VERSION_REQUERIDA) {
      mostrarError(`El archivo es un DXF versión ${NOMBRES_VERSION[v] || v} (${v}). Se requiere DXF 2010: en AutoCAD, Guardar como › «AutoCAD 2010/LT2010 DXF (*.dxf)».`);
      return;
    }

    estado.ocupado = true;
    estado.archivo = file;
    el.zona.classList.add("ocupada");
    el.zona.querySelector(".zona-carga__titulo").textContent = estado.motorListo
      ? "Analizando el archivo…"
      : "Esperando que termine de cargar el motor…";

    const r = await pedir({ tipo: "analizar", nombre: file.name, datos: buffer }, [buffer]);
    estado.ocupado = false;
    el.zona.classList.remove("ocupada");
    el.zona.querySelector(".zona-carga__titulo").innerHTML = "Arrastrá el archivo <b>.dxf</b> acá o <u>elegilo</u>";
    if (!r.ok) {
      mostrarError(r.error, r.detalle);
      return;
    }
    estado.info = r.info;
    mostrarOpciones();
  }

  // ───────────── Paso 2: opciones detectadas ─────────────
  function mostrarOpciones() {
    const info = estado.info;
    el.zona.hidden = true;
    el.opciones.hidden = false;
    el.nombre.textContent = estado.archivo.name;
    el.version.textContent = `DXF ${info.version_nombre} ✓`;

    el.selLayout.innerHTML = info.layouts.map((l) =>
      `<option value="${esc(l.nombre)}">${esc(l.nombre)} · ${l.viewports} viewport${l.viewports === 1 ? "" : "s"}</option>`
    ).join("");
    if (info.layout_sugerido) el.selLayout.value = info.layout_sugerido;
    dibujarPisos();
    paso(2);
    el.opciones.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function layoutActual() {
    return estado.info.layouts.find((l) => l.nombre === el.selLayout.value);
  }

  function dibujarPisos() {
    const info = estado.info;
    const lay = layoutActual();
    el.pisos.innerHTML = info.pisos.map((p) => {
      const n = lay ? lay.por_piso[p] : 0;
      const c = info.conteo[p];
      const det = n === 0 ? "sin viewport" : `${c.dominio} dom. · ${c.sup} sup.`;
      return `<label class="piso"><input type="checkbox" value="${esc(p)}" ${n > 0 ? "checked" : "disabled"}>` +
        `<span><b>${esc(p)}</b><small>${det}</small></span></label>`;
    }).join("");
    el.pisos.querySelectorAll("input").forEach((i) => i.addEventListener("change", validarOpciones));
    validarOpciones();
  }

  function validarOpciones() {
    const info = estado.info;
    const lay = layoutActual();
    const avisos = [];
    let bloquea = false;
    if (!info.pisos.length) { avisos.push(["grave", "No se encontraron capas M-MH-<piso>-DOMINIO en el archivo."]); bloquea = true; }
    if (!info.layouts.length) { avisos.push(["grave", "El archivo no tiene layouts de papel."]); bloquea = true; }
    if (info.estilos_faltantes.length) {
      avisos.push(["grave", `Faltan estilos de cota de la plantilla CABA: ${info.estilos_faltantes.join(", ")}.`]);
      bloquea = true;
    }
    if (lay) {
      const sinVp = info.pisos.filter((p) => lay.por_piso[p] === 0);
      const multiples = info.pisos.filter((p) => lay.por_piso[p] > 1);
      if (sinVp.length) avisos.push(["", `En «${lay.nombre}» no hay viewport para: ${sinVp.join(", ")}. Esos pisos no se pueden acotar en este layout.`]);
      if (multiples.length) avisos.push(["", `Hay más de un viewport candidato para: ${multiples.join(", ")}. Se usará el primero.`]);
    }
    if (!info.capa_caratula) avisos.push(["", "No existe la capa 01-P-PLANO-CARATULA; las cotas se crearán en ella igualmente."]);
    const elegidos = el.pisos.querySelectorAll("input:checked").length;
    el.avisosAnalisis.innerHTML = avisos.map(([c, t]) => `<li class="${c}">${esc(t)}</li>`).join("");
    el.btnAcotar.disabled = bloquea || elegidos === 0;
  }

  // ───────────── Paso 3: acotar, ver y descargar ─────────────
  async function acotar() {
    const pisos = $$("#lista-pisos input:checked").map((i) => i.value);
    const layout = el.selLayout.value;
    el.btnAcotar.disabled = true;
    el.btnAcotar.textContent = "Acotando…";
    el.errorCarga.hidden = true;

    const r = await pedir({ tipo: "acotar", pisos, layout });
    el.btnAcotar.textContent = "Acotar plano";
    el.btnAcotar.disabled = false;
    motor("listo", "Motor listo · todo corre en tu navegador");
    if (!r.ok) {
      mostrarError(`No se pudo acotar el plano: ${r.error}`, r.detalle);
      return;
    }

    estado.svgs = { oscuro: r.svg };
    estado.fondo = "oscuro";
    $$("[data-fondo]").forEach((b) => b.classList.toggle("activo", b.dataset.fondo === "oscuro"));

    el.opciones.hidden = true;
    el.resultado.hidden = false;
    paso(3);
    el.visorLayout.textContent = layout;
    mostrarSvg(r.svg, true);
    mostrarResumen(r.resumen, r.informe);
    prepararDescarga(r.archivo);
    el.resultado.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function mostrarResumen(res, informe) {
    el.total.textContent = res.total;
    el.nPisos.textContent = res.pisos.length;
    el.dup.textContent = res.skipped_dup_sup;
    el.nRevisar.textContent = res.revisar.length;
    el.cajaRevisar.classList.toggle("atencion", res.revisar.length > 0);

    const filas = Object.entries(res.stats).sort(([a], [b]) => a.localeCompare(b));
    el.tablaEstilos.innerHTML = filas.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join("") +
      `<tr class="total"><td>Total</td><td>${res.total}</td></tr>`;

    el.revisarVacio.hidden = res.revisar.length > 0;
    el.revisarIntro.hidden = res.revisar.length === 0;
    el.listaRevisar.innerHTML = res.revisar.map((r, i) =>
      `<li><button type="button" data-marca="${i}"><span class="circulo-rojo"></span>` +
      `<span><span class="num">${esc(r.medida)}</span> <span class="det">· ${esc(r.piso)} · ${esc(r.estilo)}</span></span>` +
      `<span class="det">${esc(ESTADOS[r.estado] || r.estado)}</span></button></li>`
    ).join("");

    const avisos = [...res.avisos.map((a) => ["", a])];
    if (res.errores_auditoria.length) avisos.push(["grave", `La auditoría de ezdxf encontró ${res.errores_auditoria.length} error(es). Ver el informe completo.`]);
    el.avisosResultado.innerHTML = avisos.map(([c, t]) => `<li class="${c}">${esc(t)}</li>`).join("");
    el.informe.textContent = informe.join("\n");
  }

  function prepararDescarga(buffer) {
    if (estado.urlDescarga) URL.revokeObjectURL(estado.urlDescarga);
    const blob = new Blob([buffer], { type: "application/dxf" });
    estado.urlDescarga = URL.createObjectURL(blob);
    const base = estado.archivo.name.replace(/\.dxf$/i, "").replace(/[\s_-]*sin[\s_-]*acotar\s*$/i, "");
    const nombre = `${base} ACOTADO.dxf`;
    el.btnDescargar.href = estado.urlDescarga;
    el.btnDescargar.download = nombre;
    el.descargaNombre.textContent = nombre;
  }

  // ───────────── Visor con zoom y paneo ─────────────
  const vista = { svg: null, base: null, vb: null };

  function mostrarSvg(texto, ajustar) {
    const anterior = vista.vb;
    el.visor.querySelectorAll("svg").forEach((s) => s.remove());
    const doc = new DOMParser().parseFromString(texto, "image/svg+xml");
    const svg = document.importNode(doc.documentElement, true);
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    el.visor.appendChild(svg);
    const [x, y, w, h] = svg.getAttribute("viewBox").split(/[\s,]+/).map(Number);
    vista.svg = svg;
    vista.base = { x, y, w, h };
    vista.vb = ajustar || !anterior ? { ...vista.base } : anterior;
    el.visor.style.setProperty("--visor-actual", FONDOS[estado.fondo]);
    aplicarVista();
  }

  function aplicarVista() {
    const { x, y, w, h } = vista.vb;
    vista.svg.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
  }

  function aSvg(clientX, clientY) {
    const pt = vista.svg.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    return pt.matrixTransform(vista.svg.getScreenCTM().inverse());
  }

  function zoomEn(factor, clientX, clientY) {
    if (!vista.svg) return;
    const vb = vista.vb;
    const nuevoAncho = vb.w / factor;
    if (nuevoAncho > vista.base.w * 3 || nuevoAncho < vista.base.w / 4000) return;
    const p = aSvg(clientX, clientY);
    vista.vb = {
      x: p.x - (p.x - vb.x) / factor, y: p.y - (p.y - vb.y) / factor,
      w: nuevoAncho, h: vb.h / factor,
    };
    aplicarVista();
  }

  function zoomCentro(factor) {
    const r = el.visor.getBoundingClientRect();
    zoomEn(factor, r.left + r.width / 2, r.top + r.height / 2);
  }

  function ajustar() {
    if (!vista.svg) return;
    vista.vb = { ...vista.base };
    aplicarVista();
  }

  function unidadesPorPixel() {
    const r = el.visor.getBoundingClientRect();
    return Math.max(vista.vb.w / r.width, vista.vb.h / r.height);
  }

  const punteros = new Map();
  let pinchInicial = null;

  el.visor.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018));
    zoomEn(factor, e.clientX, e.clientY);
  }, { passive: false });

  el.visor.addEventListener("pointerdown", (e) => {
    if (!vista.svg) return;
    el.visor.setPointerCapture(e.pointerId);
    punteros.set(e.pointerId, { x: e.clientX, y: e.clientY });
    el.visor.classList.add("moviendo");
    if (punteros.size === 2) {
      const [a, b] = [...punteros.values()];
      pinchInicial = Math.hypot(a.x - b.x, a.y - b.y);
    }
  });

  el.visor.addEventListener("pointermove", (e) => {
    if (!punteros.has(e.pointerId)) return;
    const previo = punteros.get(e.pointerId);
    const actual = { x: e.clientX, y: e.clientY };
    punteros.set(e.pointerId, actual);
    if (punteros.size === 1) {
      const k = unidadesPorPixel();
      vista.vb.x -= (actual.x - previo.x) * k;
      vista.vb.y -= (actual.y - previo.y) * k;
      aplicarVista();
    } else if (punteros.size === 2 && pinchInicial) {
      const [a, b] = [...punteros.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomEn(d / pinchInicial, (a.x + b.x) / 2, (a.y + b.y) / 2);
      pinchInicial = d;
    }
  });

  function soltar(e) {
    punteros.delete(e.pointerId);
    if (punteros.size < 2) pinchInicial = null;
    if (punteros.size === 0) el.visor.classList.remove("moviendo");
  }
  el.visor.addEventListener("pointerup", soltar);
  el.visor.addEventListener("pointercancel", soltar);
  el.visor.addEventListener("dblclick", ajustar);
  el.visor.addEventListener("keydown", (e) => {
    if (e.key === "+" || e.key === "=") zoomCentro(1.4);
    else if (e.key === "-") zoomCentro(1 / 1.4);
    else if (e.key === "0") ajustar();
  });

  $$("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.zoom === "mas") zoomCentro(1.5);
    else if (b.dataset.zoom === "menos") zoomCentro(1 / 1.5);
    else ajustar();
  }));

  $$("[data-fondo]").forEach((b) => b.addEventListener("click", async () => {
    const fondo = b.dataset.fondo;
    if (fondo === estado.fondo || !vista.svg) return;
    $$("[data-fondo]").forEach((x) => x.classList.toggle("activo", x === b));
    estado.fondo = fondo;
    if (!estado.svgs[fondo]) {
      el.visorCargando.hidden = false;
      const r = await pedir({ tipo: "vista", fondo });
      el.visorCargando.hidden = true;
      motor("listo", "Motor listo · todo corre en tu navegador");
      if (!r.ok) { console.error(r); return; }
      estado.svgs[fondo] = r.svg;
    }
    if (estado.fondo === fondo) mostrarSvg(estado.svgs[fondo], false);
  }));

  // Marcas de revisión: círculos dibujados con el color COLOR_MARCAS.
  function marcasEnSvg() {
    const clases = [];
    vista.svg.querySelectorAll("style").forEach((s) => {
      const re = /\.(C\d+)\s*\{[^}]*stroke:\s*([^;]+);/g;
      let m;
      while ((m = re.exec(s.textContent))) if (m[2].trim().toLowerCase() === COLOR_MARCAS) clases.push(m[1]);
    });
    return clases.length ? Array.from(vista.svg.querySelectorAll(clases.map((c) => `path.${c}`).join(","))) : [];
  }

  el.listaRevisar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-marca]");
    if (!b || !vista.svg) return;
    const marca = marcasEnSvg()[Number(b.dataset.marca)];
    if (!marca) return;
    const bb = marca.getBBox();
    const lado = Math.max(bb.width, bb.height) * 7;
    const r = el.visor.getBoundingClientRect();
    const h = lado * (r.height / r.width);
    vista.vb = { x: bb.x + bb.width / 2 - lado / 2, y: bb.y + bb.height / 2 - h / 2, w: lado, h };
    aplicarVista();
    el.visor.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  // ───────────── Eventos generales ─────────────
  el.input.addEventListener("change", () => cargarArchivo(el.input.files[0]));
  el.selLayout.addEventListener("change", dibujarPisos);
  el.btnAcotar.addEventListener("click", acotar);

  ["dragenter", "dragover"].forEach((t) => el.zona.addEventListener(t, (e) => {
    e.preventDefault();
    el.zona.classList.add("arrastrando");
  }));
  ["dragleave", "drop"].forEach((t) => el.zona.addEventListener(t, (e) => {
    e.preventDefault();
    el.zona.classList.remove("arrastrando");
  }));
  el.zona.addEventListener("drop", (e) => cargarArchivo(e.dataTransfer.files[0]));
  // Evita que soltar un archivo fuera de la zona lo abra en el navegador.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  $$('[data-accion="otro-archivo"]').forEach((b) => b.addEventListener("click", () => {
    reiniciar();
    $("#herramienta").scrollIntoView({ behavior: "smooth" });
  }));

  $$('[data-accion="ejemplo"]').forEach((b) => b.addEventListener("click", async () => {
    reiniciar();
    $("#herramienta").scrollIntoView({ behavior: "smooth" });
    try {
      const resp = await fetch("ejemplos/ejemplo-sin-acotar.dxf");
      if (!resp.ok) throw new Error(resp.status);
      const blob = await resp.blob();
      cargarArchivo(new File([blob], "ejemplo-sin-acotar.dxf"));
    } catch (err) {
      mostrarError("No se pudo descargar el archivo de ejemplo.");
    }
  }));

  // En GitHub Pages (usuario.github.io/repo) el enlace a la documentación
  // apunta al archivo en GitHub, donde el Markdown se ve formateado.
  const m = location.hostname.match(/^([\w-]+)\.github\.io$/i);
  const repo = location.pathname.split("/").filter(Boolean)[0];
  if (m && repo) {
    $$('a[href="docs/REGLA-ACOTAMIENTO.md"]').forEach((a) => {
      a.href = `https://github.com/${m[1]}/${repo}/blob/main/docs/REGLA-ACOTAMIENTO.md`;
    });
  }
})();

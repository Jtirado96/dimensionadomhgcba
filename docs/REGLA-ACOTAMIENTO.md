# La regla de acotamiento, en detalle

Este documento explica la lógica implementada en `acotar_mh.py`: por qué cada
decisión se toma como se toma, qué casos límite se investigaron, y qué
limitaciones de la librería `ezdxf` hubo que esquivar para que el resultado
sea indistinguible de una cota dibujada a mano en AutoCAD.

Fue reverse-engineered a partir de planos MH reales de la Ciudad de Buenos
Aires, cruzando la convención documentada en *Detalle_Layers_Bloques4.pdf*
(v4, plantillas oficiales de Modernización del Trámite de Mensuras) con el
comportamiento observado en archivos acotados a mano por un agrimensor.

## Archivo de entrada: DXF 2010

Solo se aceptan archivos guardados como **AutoCAD 2010/LT2010 DXF** en formato
texto (`$ACADVER = AC1024`). Antes de leer el plano se inspecciona el
encabezado y cualquier otra versión, o un DXF binario, se rechaza con un
mensaje que explica cómo volver a exportarlo. Hay dos motivos:

- Desde la versión 2007 el DXF se guarda siempre en UTF-8. Los DXF anteriores
  usan la página de códigos del sistema (por ejemplo cp1252), y nombres como
  `MH-Común-Arriba` o `Lado-Polígono-Superficie` podrían leerse mal.
- Es la versión con la que se validó la lógica contra planos reales
  (incluido el parche de `DIMPOST`, que depende de cómo exporta `ezdxf` las
  versiones R2007 en adelante).

El archivo de salida se guarda en la misma versión, DXF 2010.

## Alcance de capas y colores

- `M-MH-<piso>-DOMINIO`: polilíneas cerradas. Color 3 (verde) = Unidad
  funcional o complementaria → estilo `MH-Unidad-*`. Color 2 (amarillo) =
  Común → estilo `MH-Común-*`.
- `M-MH-<piso>-SUP`: se dimensiona, con el estilo único
  `Lado-Polígono-Superficie`, toda polilínea cuyo color **no** sea 1
  (rojo/cubierta), 7 (blanco/muro común ext. o columna — se procesa aparte,
  ver "Tubo y columna" más abajo), 256 (bylayer) ni 6 (magenta/apoyo). El
  color 6 se usa para polígonos de apoyo que un agrimensor completa a mano
  en las planillas de superficies, y nunca debe acotarse.

## Elección del estilo Arriba/Abajo

Para cada lado del polígono hay que decidir dos cosas: de qué lado del lado
va el texto (debe quedar hacia adentro del polígono) y si el estilo es
`...-Arriba` (sobrelínea, código `\O<>` en el DIMPOST) o `...-Abajo`
(subrayado, `\L<>`) — la diferencia entre uno y otro es la que hace que el
número se lea "parado" correctamente para quien mira el plano, sin tener que
girar la cabeza.

**Normal interior**: en vez de asumir el sentido de giro global del polígono
(que falla en polígonos cóncavos o con entrantes), se genera un punto de
prueba a una tolerancia infinitesimal (1 mm) del punto medio de cada lado, a
cada lado del lado (sobre cada una de las dos normales posibles), y se
testea con un test punto-en-polígono (`shapely.Polygon.contains`) cuál de los
dos cae adentro. El sentido de giro (shoelace) se usa solo como respaldo en
casos degenerados.

**Arriba vs. Abajo**: se normaliza la dirección del lado a una "dirección de
lectura" (que su componente X sea ≥ 0, o Y ≥ 0 si es vertical) y se compara
por producto cruzado contra la normal interior ya encontrada: si la normal
queda a la izquierda de esa dirección de lectura → `Arriba`; a la derecha →
`Abajo`. Este mismo ángulo de lectura (normalizado al rango (-90°, 90°]) se
reutiliza después para dos cosas más: orientar el rectángulo que aproxima el
texto (para la búsqueda de posición sin colisión) y fijar la rotación real
del `MTEXT` renderizado (ver "Rotación del texto" más abajo).

## Deduplicación DOMINIO vs. SUPERFICIE

Antes de dimensionar un lado de SUPERFICIE (o de una columna, ver más abajo)
se compara contra el conjunto de lados de todos los polígonos DOMINIO del
mismo piso: ambos extremos, redondeados a 3 decimales (milímetro de
precisión), en cualquier orden. Si coincide exactamente, se omite — ya está
cubierto por la cota de DOMINIO. Cuando los lados no coinciden exactamente
(mismo tramo de pared pero subdividido distinto en SUP y en DOMINIO), ambas
cotas se generan y quedan naturalmente separadas por el sistema de
colocación sin colisión.

## Colocación del texto: contención completa + búsqueda sin colisión

El texto de cada cota se aproxima como un rectángulo orientado (shapely
`Polygon`), con ancho proporcional a la cantidad de caracteres del número
formateado y alto igual a la altura de texto del estilo, orientado según el
ángulo de lectura del lado.

Para encontrar dónde ubicarlo, se prueba una grilla de posiciones: distintas
distancias perpendiculares al lado (múltiplos de un offset "ideal" único,
0.25 m reales, usado tanto para DOMINIO como para SUPERFICIE) combinadas con
pequeños corrimientos a lo largo del propio lado — esto último es lo que
permite ubicar dos cotas "una al lado de la otra" en vez de superpuestas,
cuando dos lados cercanos de capas distintas quedarían, si no, a la misma
distancia del borde.

De todas las posiciones candidatas, se elige la primera que deja el
rectángulo de texto **totalmente contenido** en el polígono real y que
además no se superpone con ningún rectángulo de texto ya colocado en el
mismo piso. Si ninguna combinación de distancia/corrimiento logra ambas
condiciones con el tamaño de texto normal, se reduce progresivamente el
tamaño real del rótulo (hasta un 42% del tamaño de estilo) y se repite la
búsqueda completa. Solo como último recurso — un lado más angosto que el
texto incluso reducido al mínimo — se cae a garantizar únicamente que el
**centro** del texto quede dentro del polígono; esto queda reportado
explícitamente en el resumen de ejecución (`fallback_center_only`) para que
se pueda revisar a mano si hace falta.

## Tubo y columna

Los polígonos color blanco (7) dentro de la capa SUP representan columnas o
tramos de muro común exterior. Son formas demasiado chicas para que el texto
entre adentro, así que se dimensionan con una regla espejada:

- Mismo cálculo de normal interior que el resto.
- El booleano Arriba/Abajo se calcula igual que para "Común" (mismo producto
  cruzado) pero se **invierte** el resultado.
- El estilo final reutiliza `MH-Común-Arriba`/`MH-Común-Abajo` (con su
  sobrelínea/subrayado correspondiente al booleano ya invertido).
- El texto se ubica del lado **exterior** del polígono (la normal opuesta a
  la interior) — con el mismo sistema de búsqueda sin colisión, pero exigiendo
  que el rectángulo de texto no invada el propio polígono de la columna, en
  vez de exigir que quede contenido adentro.

## Limitaciones de `ezdxf` y cómo se esquivaron

Para los estilos usados en estos planos (sin flecha, sin línea de cota
visible, solo el texto con sobre/subrayado) se detectaron tres
comportamientos de `ezdxf` (1.4.4) que no coinciden con una cota nativa de
AutoCAD con el mismo estilo:

1. **Flechas fantasma**: `add_aligned_dim(...).render()` inserta un bloque de
   flecha (p. ej. `_CLOSEDFILLED`) a escala 1.0 en el bloque de geometría de
   la dimensión, aunque el estilo tenga `DIMASZ=0`. A la escala de papel
   típica de un plano (~1:100) esto se ve como un triángulo gigante tapando
   el dibujo. Una cota nativa de AutoCAD con el mismo estilo solo tiene
   `MTEXT` + `POINT` en su bloque de geometría, nunca `INSERT` — así que se
   eliminan a mano todas las entidades `INSERT` después de renderizar.

2. **Posición del texto poco confiable**: el parámetro `distance` de
   `add_aligned_dim` no se comporta de forma consistente a las escalas de
   papel muy chicas usadas en estos planos — en pruebas puntuales llegó a
   colocar el texto del lado y a la distancia equivocados. Se evita pasando
   siempre `distance=0.0` y sobrescribiendo directamente, después de
   renderizar, el punto de inserción del `MTEXT` del bloque de geometría
   (además de `dimension.dxf.text_midpoint` y `dimension.dxf.defpoint`) con
   el punto calculado explícitamente por el sistema de colocación.

3. **Rotación del texto**: ezdxf orienta el `MTEXT` renderizado según el
   ángulo crudo del segmento `p1→p2` tal como se lo pasa a
   `add_aligned_dim` — que depende únicamente del orden en que la polilínea
   original define sus vértices, y puede caer en cualquiera de los 360°,
   incluida la mitad del círculo que queda "cabeza abajo" para un lector
   humano. Se sobrescribe `MTEXT.dxf.rotation` con el ángulo de lectura ya
   normalizado (ver "Elección del estilo Arriba/Abajo").

4. **DIMPOST no se exporta para DXF moderno**: asignar
   `dimstyle.dxf.dimpost` (el prefijo `\L<>`/`\O<>` a nivel de estilo) antes
   de guardar funciona en memoria, pero el valor se pierde al guardar y
   releer el archivo — `ezdxf` 1.4.4 no incluye `dimpost`/`dimapost` en su
   tabla de exportación para DXF R2007 en adelante (sí lo hace para R12 y
   R2000). El código de grupo DXF 3 (`dimpost`) sigue siendo válido en la
   especificación para cualquier versión, así que se agrega a mano con un
   post-proceso de texto sobre el archivo DXF ya guardado
   (`patch_dimpost()`): ubica cada tabla DIMSTYLE por su nombre e inserta el
   tag correspondiente justo después del tag de flags, replicando el orden
   que usa `ezdxf` para R12/R2000.

En todos los casos, el patrón es el mismo: no confiar en que `ezdxf` calcule
o exporte correctamente un atributo visual de la dimensión para estos
estilos de línea/flecha suprimidos, y en cambio dejar que
`add_aligned_dim(...).render()` genere el bloque, para después sobrescribir
a mano cada atributo real (posición, texto, tamaño, rotación, prefijo) con
el valor calculado explícitamente a partir de la geometría del polígono.

## Formato del texto

El número de cada cota se construye explícitamente en Python
(`f"{longitud:.2f}"`, sobre la longitud real calculada en espacio modelo) en
vez de dejar que AutoCAD/ezdxf lo formatee a partir del header DXF o del
estilo — esto garantiza separador decimal de punto y exactamente dos
decimales en el 100% de los casos, independientemente de la configuración
regional del archivo de origen. Para DOMINIO y columnas, el número se
envuelve manualmente con el prefijo `\O` (Arriba) o `\L` (Abajo), replicando
el patrón DIMPOST de los estilos `MH-*`; SUPERFICIE no usa prefijo.

## Transformación Model → Paper

Cada layout tiene un `VIEWPORT` por piso, que se identifica por ser el único
cuya lista de capas congeladas (`frozen_layers`) **no** incluye la capa
`M-MH-<piso>-DOMINIO` de ese piso. A partir de ahí:

```
scale = viewport.height / viewport.view_height          # unidades de papel por unidad de modelo
paper_point = viewport.center + (model_point - viewport.view_center_point) * scale
```

Esta transformación es pura escala + traslación (sin rotación ni espejado),
así que un ángulo calculado en espacio modelo (por ejemplo, el ángulo de
lectura de un lado) es directamente válido en espacio papel sin necesidad de
transformarlo.

Cada dimensión lleva además un override `dimlfac = 1 / scale` (vía XDATA
`DSTYLE`), para que el texto siga mostrando la medida real del terreno
aunque los puntos de la dimensión estén en espacio papel.

## Validación

Después de generar un archivo, conviene verificar programáticamente (con
`ezdxf`, recorriendo las entidades `DIMENSION` del layout) al menos:

- Que ninguna entidad `INSERT` haya quedado en el bloque de geometría de
  ninguna dimensión (flechas fantasma).
- Que el texto de cada cota matchee `\d+\.\d{2}` (con prefijo `\O`/`\L`
  opcional) — formato numérico correcto.
- Que la rotación del `MTEXT` de cada dimensión esté dentro del rango
  legible (-90°, 90°].
- Que `doc.audit()` no reporte errores estructurales.
- Reconstruyendo los polígonos reales en espacio papel (vía la misma
  transformación de viewport) y el rectángulo de texto de cada cota, que el
  rectángulo quede contenido en el polígono correcto (o, para columnas, que
  no lo invada).

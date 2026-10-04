# munincloud-conector-csv

**MuninCloud · conector CSV genérico.** Convierte las respuestas de la **API de auditoría de facturas de MuninCloud** (formato `v1`) en dos ficheros CSV
que cualquier programa contable o una hoja de cálculo puede importar:

- **`facturas.csv`**: una fila por factura (número, fecha, partes, totales, cuadre y si conviene revisarla).
- **`iva.csv`**: una fila por cada tipo de IVA de cada factura (base y cuota), con los suplidos aparte.

Es un único fichero de Python sin dependencias (`munincloud_csv.py`, más la pieza común `munincloud_v1.py`). Cógelo,
léelo y adáptalo a tu programa: para eso está. Licencia MIT (`LICENSE`).

## Requisitos

Python 3.8 o posterior (solo biblioteca estándar). Probado con Python 3.12; con 3.8 se ha comprobado la sintaxis.

## En cinco minutos

Desde la carpeta del repositorio, con las respuestas de ejemplo que trae (facturas de prueba con NIF reservados por
la AEAT para pruebas):

```bash
python3 munincloud_csv.py --mi-nif B00000018 --salida salida ejemplos/entrada
head -3 salida/facturas.csv
head -3 salida/iva.csv
python3 -m unittest discover -s pruebas
```

Con tus facturas: guarda cada respuesta de `GET https://api.munincloud.com/v1/trabajos/{trabajo}` como un `.json` en
una carpeta y pasa esa carpeta (o los ficheros sueltos). `--mi-nif` es el NIF de **tu** empresa: con él se comprueba
la dirección que da la API (ver «Compra o venta»).

```bash
python3 munincloud_csv.py --mi-nif TU_NIF --salida salida carpeta_con_respuestas/
python3 munincloud_csv.py --mi-nif TU_NIF --excel-es --salida salida_excel carpeta_con_respuestas/
```

`--excel-es` escribe coma decimal y la marca UTF-8 (BOM) para abrirlo con doble clic en un Excel en español. Sin él:
separador `;`, punto decimal, UTF-8 y fin de línea CRLF, que es lo que conviene indicar al importarlo en otro programa.

## Un ejemplo

Entrada (recortada): la respuesta `v1` de una compra con dos tipos de IVA y retención.

```json
{"trabajo": "trb_00000000000000000000000003", "estado": "terminado",
 "documento": {
   "proveedor": {"nombre": "OPERACION NO IDENTIFICADA EN CONSTITUCIÓN", "cif_nif": "B99999997"},
   "cliente": {"nombre": "TEST SL", "cif_nif": "B00000018"},
   "factura": {"numero": "F7-C-0001", "fecha": "2026-09-25", "base_total": 150.0, "iva_total": 26.0,
               "total_factura": 161.0},
   "impuestos_y_retenciones": [{"tipo": "IRPF", "rate": -15.0, "importe": -15.0}],
   "lineas": [{"amount": 100.0, "tax_percent": 21.0}, {"amount": 50.0, "tax_percent": 10.0}], "...": "..."}}
```

Salida en `iva.csv` (columnas recortadas):

```
"documento";"direccion";"numero";"clase";"tipo_iva";"base";"cuota"
"compras__mixta_irpf.json";"RECIBIDA";"F7-C-0001";"IVA";"21";"100.00";"21.00"
"compras__mixta_irpf.json";"RECIBIDA";"F7-C-0001";"IVA";"10";"50.00";"5.00"
```

Y en `facturas.csv`: base `150.00`, cuota `26.00`, retención `15.00` al `15` %, total `161.00`, descuadre `0.00`,
`cuadra` = `SI`, `revisar` = `NO`. Las salidas completas de las 19 respuestas de ejemplo están en `ejemplos/salida/`
(15 facturas; de las otras cuatro, dos son el mismo documento, un acta, que la API rechazó con `NO_ES_FACTURA`, una
es un documento con varias facturas que rechazó con `MULTIFACTURA` y otra un trabajo aún `en_curso`: van a
`omitidos.csv`).

## Compra o venta: cómo lo decide

Desde `v1.1` la respuesta lo dice: **`documento.direccion`** vale `RECIBIDA` (una compra tuya) o `EMITIDA` (una venta
tuya). Desde `v1.3` es la que **tú declaras al subir** la factura (`tipo=compra` → `RECIBIDA`, `tipo=venta` →
`EMITIDA`); si en el papel tu empresa aparece en el lado contrario, la respuesta trae el aviso `TIPO_DISCREPANTE` y la
factura sale para revisar (`AVISO_TIPO_DISCREPANTE`). El conector **lo lee**, no lo deduce. Si le das `--mi-nif` y la respuesta no trae
ese aviso (una anterior a `v1.3`), además lo **comprueba** contra el papel: si tu NIF aparece solo en el otro lado, la factura sale para revisar con
`DIRECCION_CONTRADICE_NIF` (manda lo que dice la API, pero no se importa a ciegas).

Solo si la respuesta **no** trae `direccion` (una anterior a `v1.1`), el conector la decide con `--mi-nif`. La
respuesta llama `proveedor` **siempre** a quien emite y `cliente` **siempre** a quien recibe, también en tus ventas; y
`lectura_papel` trae el `emisor` y el `receptor` leídos del papel:

| tu NIF está en… | `direccion` | la contraparte es |
|---|---|---|
| solo el lado del emisor | `EMITIDA` (una venta tuya) | el receptor |
| solo el lado del receptor | `RECIBIDA` (una compra tuya) | el emisor |
| en ninguno, o en los dos | `DESCONOCIDA` → se marca para revisar | (vacía) |

`--direccion RECIBIDA` o `--direccion EMITIDA` fuerza la de todo el lote (manda sobre la de la API).

## Columnas

**`facturas.csv`**

| columna | qué es |
|---|---|
| `documento`, `trabajo` | el fichero de entrada y el identificador del trabajo en la API |
| `direccion` | `RECIBIDA`, `EMITIDA` o `DESCONOCIDA` (ver arriba) |
| `numero`, `fecha`, `divisa` | la fecha como `AAAA-MM-DD`, y vacía si no existe en el calendario |
| `emisor_nombre`, `emisor_nif`, `receptor_nombre`, `receptor_nif` | las dos partes, tal como se leyeron |
| `contraparte_nombre`, `contraparte_nif`, `contraparte_pais` | la otra parte según la dirección; el país sale del NIF (`ES`, o el prefijo de un VAT europeo) |
| `es_rectificativa`, `rectifica_a_numero`, `rectifica_a_fecha` | `SI`/`NO` y la factura que rectifica |
| `base_imponible`, `cuota_iva`, `total_factura` | los de la cabecera de la respuesta |
| `suplidos_no_sujetos` | lo que va fuera de la base y dentro del total (suplidos, tasas) |
| `recargo_equivalencia` | el `factura.recargo_total` de la respuesta si lo trae (`v1.1`); si no, solo si la factura dice que lo lleva, calculado por el art. 161 LIVA (5,2 / 1,4 / 0,5 %) sobre cada base. Vacío si no |
| `retencion`, `porcentaje_retencion` | la retención (en positivo) y su tipo, si viene |
| `descuadre` | base + IVA + suplidos + recargo − retención − total. `0.00` cuando cuadra |
| `cuadra` | `SI` si el descuadre es menor de 2 céntimos |
| `estado_nif_aeat` | lo que dijo la AEAT del NIF (`IDENTIFICADO`, …) si se comprobó |
| `revisar`, `motivos_revision` | `SI` y los motivos si conviene mirarla antes de contabilizarla (ver abajo) |
| `avisos_api`, `dictamen` | los avisos de la API y los hallazgos de la auditoría, tal cual |

**`iva.csv`**: `documento`, `trabajo`, `direccion`, `numero`, `fecha`, `contraparte_nif`, y por registro:

| columna | qué es |
|---|---|
| `clase` | `IVA`, `NO_SUJETO` (suplidos) o `AJUSTE` (lo que las líneas no explican de la cabecera) |
| `tipo_iva`, `base`, `cuota` | la cuota es la suma de base × tipo de cada línea, redondeada a céntimos línea a línea |
| `tipo_recargo`, `cuota_recargo` | solo con recargo de equivalencia. El que viene en `recargo_total` se pone en el registro si hay un único tipo de IVA y su porcentaje legal da ese importe; si no, la factura sale con `RECARGO_SIN_DESGLOSE` |
| `origen` | `LINEAS` (sumado de las líneas), `CABECERA` (factura sin líneas: un registro con el tipo que explica cuota/base) o `AJUSTE` |

**`omitidos.csv`**: lo que no era una factura terminada (un documento que la API rechazó, un trabajo en curso, un
fichero ilegible), con el motivo. No se pierde nada en silencio.

Todas las celdas van entre comillas y, si un texto empieza como una fórmula (`=`, `+`, `-`, `@`), lleva un
apóstrofo delante para que la hoja de cálculo no la ejecute; los importes negativos siguen siendo números.

## Cuándo una factura sale para revisar

| motivo | qué pasa |
|---|---|
| `DIRECCION_DESCONOCIDA` | la respuesta no trae `direccion` y tu NIF no está en un solo lado |
| `DIRECCION_CONTRADICE_NIF` | la respuesta dice una dirección y tu NIF está solo en el lado de la otra (solo si no trae `TIPO_DISCREPANTE`: no se cuenta dos veces) |
| `NIF_AEAT_BAJA_O_REVOCADO` | la AEAT da el NIF de la otra parte `IDENTIFICADO-BAJA` o `IDENTIFICADO-REVOCADO` (`verificacion_identidad.situacion`) |
| `NIF_AEAT_NO_IDENTIFICADO` | la AEAT da el NIF de la otra parte `NO IDENTIFICADO` (`verificacion_identidad.situacion`) |
| `DIVISA_NO_EUR` | la factura no viene en euros: los importes no se convierten y ningún programa recibe la divisa |
| `NUMERO_NO_LEIDO`, `FECHA_NO_VALIDA`, `NIF_CONTRAPARTE_NO_LEIDO` | falta un dato que cualquier programa exige |
| `IMPORTES_NO_LEIDOS`, `SIN_IMPORTES` | falta la cabecera, o la factura no trae ningún importe |
| `DESCUADRE` | base + IVA + suplidos + recargo − retención no da el total (solo si la respuesta no trae el aviso `DESCUADRE`: no se cuenta dos veces; la cifra de `descuadre` sale igual) |
| `DESGLOSE_NO_CUADRA` | las líneas no reconstruyen la base o el IVA de la cabecera por más de 2 céntimos (sale una fila `AJUSTE` en `iva.csv`) |
| `POSIBLE_DUPLICADA` | la misma factura ya venía antes en el lote (abajo) |
| `TIPO_IVA_DESCONOCIDO`, `RECARGO_SIN_TIPO`, `RETENCION_SIN_IMPORTE` | un tipo que no es de los vigentes, o una retención sin importe |
| `RECARGO_SIN_DESGLOSE` | la respuesta trae un `recargo_total` que no se puede poner en un tipo de IVA (varios tipos, o no da el porcentaje legal) |
| `AVISO_<código>` | la API puso un aviso (`LECTURA_INCOMPLETA`, `DESCUADRE`, …). Un código que no conozcas: revísalo |
| `DICTAMEN_<código>` | la auditoría dejó un hallazgo con gravedad `rechazado` o `aviso` |

El conector **no arregla** nada de esto: lo enseña. Una factura que no cuadra sale con su descuadre a la vista.

**Duplicadas y redondeo** (pieza común 1.3.0). Si el lote trae la misma factura dos veces (misma dirección, NIF de la otra parte, número —solo letras y cifras— y fecha), la primera sigue su camino y la segunda y siguientes salen a revisar con `POSIBLE_DUPLICADA`: nunca se funden. Si las líneas difieren de la cabecera en 1 o 2 céntimos (de base, de IVA o de los dos), es redondeo del papel: se funde en la línea y el registro de IVA de mayor base, lo importable cuadra **exacto** con la cabecera y la factura no se aparta por eso; este formato no tiene fichero de avisos y la columna `revisar` de `facturas.csv` es solo de lo apartado, así que el ajuste no deja fila (va en `analizar()["notas"]` de la pieza) y no sale fila `AJUSTE` en `iva.csv`. Con 3 céntimos o más, `DESGLOSE_NO_CUADRA` y a revisar, como siempre.

## Lo que la respuesta `v1` no trae, y este conector no inventa

- **La cuenta contable** (PGC) y **el régimen de IVA**: no vienen en `v1`. Si tu programa los pide, ponlos tú.
- **El importe del recargo de equivalencia** cuando la respuesta no lo trae (`factura.recargo_total` vacío) y la
  factura no declara que lo lleva: si el total lo incluye, la factura sale con `DESCUADRE` y para revisar, en vez de
  suponer que la diferencia es el recargo. Es lo que pasa hoy con `ejemplos/entrada/ventas__recargo.json`; la misma
  factura con el recargo en la respuesta (`pruebas/datos/venta_con_recargo.json`) cuadra.
- **La cuota de IVA por línea**: `v1` trae base y tipo; la cuota se calcula línea a línea y, si no da la de la
  cabecera, la diferencia sale en una fila `AJUSTE`.
- Lo que no se pudo leer (la API de MuninCloud deja «NO … VISIBLE» o `S/N` en el campo) sale **vacío**, nunca con el centinela dentro.

## Contra qué se ha verificado

- **2026-10-04** · Sin programa de destino: el CSV genérico no es el formato de ningún programa concreto, así que no se
  ha importado en ninguno · Las 19 respuestas de `ejemplos/entrada/` (15 facturas, 4 que no lo son) pasan las pruebas
  de `pruebas/`, el banco común del kit de conectores de MuninCloud y la **vía B** (`pruebas/via_b.py`): otro lector,
  que no comparte código con el conector, rehace en céntimos enteros cada factura desde la respuesta y la coteja con
  `facturas.csv`, `iva.csv` y `omitidos.csv`; sale en rojo si se cambia un importe o se borra una fila.

Lo que no figura aquí **no está verificado** en el programa: la salida cumple el formato documentado, nada más.

## Pruebas

```bash
python3 -m unittest discover -s pruebas -v
python3 munincloud_csv.py --mi-nif B00000018 --salida salida ejemplos/entrada && python3 pruebas/via_b.py salida ejemplos/entrada
```

Comprueban, sobre los ejemplos y sobre tres respuestas de `pruebas/datos/` (una cuota que no cuadra y una factura
sin número ni NIF, desafinadas a propósito; y la venta con recargo con su `recargo_total`), que cada factura cuadra o
sale marcada, que los registros de IVA reconstruyen la cabecera, que compra y venta no se confunden y que la salida
es la de `ejemplos/salida/`.

`munincloud_v1.py` es la misma pieza en todos los conectores de MuninCloud: si la cambias, cámbiala en todos.

`pruebas/test_kit.py` son las tres pruebas mínimas de todo conector de MuninCloud; `pruebas/via_b.py`, la vía B de
arriba (imprime `VIA_B=OK` o cada `FALLO` con su factura).

## Marcas

Este conector no nombra ningún programa de terceros: no hay marcas ajenas que citar.

## Licencia

MIT. Copyright (c) 2026 Bogarra Integrated SL. Ver `LICENSE`.

#!/usr/bin/env python3
"""De la respuesta `v1` de la API de auditoría de facturas de MuninCloud a dos CSV que cualquier programa importa.

    python3 munincloud_csv.py --mi-nif B00000018 --salida salida/ trabajos/

Escribe en --salida:
  facturas.csv   una fila por factura
  iva.csv        una fila por cada tipo de IVA de cada factura (más suplidos y ajustes, si los hay)
  omitidos.csv   lo que no era una factura terminada (rechazos, trabajos en curso, ficheros ilegibles)

Las columnas están explicadas en README.md. Separador ';', punto decimal y UTF-8; con --excel-es, coma decimal y
marca UTF-8 (BOM) para abrirlo con doble clic en un Excel en español.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import munincloud_v1 as v1  # noqa: E402

VERSION = "1.2.0"

COLUMNAS_FACTURAS = [
    "documento", "trabajo", "direccion", "numero", "fecha", "divisa",
    "emisor_nombre", "emisor_nif", "receptor_nombre", "receptor_nif",
    "contraparte_nombre", "contraparte_nif", "contraparte_pais",
    "es_rectificativa", "rectifica_a_numero", "rectifica_a_fecha",
    "base_imponible", "cuota_iva", "suplidos_no_sujetos", "recargo_equivalencia",
    "retencion", "porcentaje_retencion", "total_factura",
    "descuadre", "cuadra", "estado_nif_aeat", "revisar", "motivos_revision", "avisos_api", "dictamen",
]
COLUMNAS_IVA = [
    "documento", "trabajo", "direccion", "numero", "fecha", "contraparte_nif",
    "clase", "tipo_iva", "base", "cuota", "tipo_recargo", "cuota_recargo", "origen",
]
COLUMNAS_OMITIDOS = ["documento", "trabajo", "motivo"]


def _si(b):
    return "SI" if b else "NO"


def filas(entradas, mi_nif="", direccion="", coma_decimal=False):
    """(filas_facturas, filas_iva, filas_omitidos) como listas de diccionarios de texto."""
    imp = lambda x: v1.importe(x, coma_decimal)  # noqa: E731
    tipo = lambda x: v1.texto_tipo(x).replace(".", ",") if coma_decimal else v1.texto_tipo(x)  # noqa: E731
    facturas, ivas, omitidos = [], [], []
    dup = v1.duplicadas(entradas, mi_nif, direccion)       # la misma factura dos veces en el lote: la 2.ª a revisar
    for e, duplicada_de in zip(entradas, dup):
        if e["documento"] is None:
            omitidos.append({"documento": e["origen"], "trabajo": e["trabajo"], "motivo": e["omitido"]})
            continue
        a = v1.analizar(e["documento"], mi_nif, direccion, duplicada_de)
        facturas.append({
            "documento": e["origen"], "trabajo": e["trabajo"], "direccion": a["direccion"],
            "numero": a["numero"], "fecha": a["fecha"], "divisa": a["divisa"],
            "emisor_nombre": a["emisor"]["nombre"], "emisor_nif": a["emisor"]["nif"],
            "receptor_nombre": a["receptor"]["nombre"], "receptor_nif": a["receptor"]["nif"],
            "contraparte_nombre": a["contraparte"]["nombre"], "contraparte_nif": a["contraparte"]["nif"],
            "contraparte_pais": a["contraparte"]["pais"],
            "es_rectificativa": _si(a["es_rectificativa"]),
            "rectifica_a_numero": a["rectifica_numero"], "rectifica_a_fecha": a["rectifica_fecha"],
            "base_imponible": imp(a["base"]), "cuota_iva": imp(a["iva"]),
            "suplidos_no_sujetos": imp(a["suplidos"]), "recargo_equivalencia": imp(a["recargo"]),
            "retencion": imp(a["retencion"]), "porcentaje_retencion": tipo(a["porcentaje_retencion"]),
            "total_factura": imp(a["total"]),
            "descuadre": imp(a["descuadre"]), "cuadra": _si(a["cuadra"]),
            "estado_nif_aeat": a["estado_nif_aeat"], "revisar": _si(a["revisar"]),
            "motivos_revision": " | ".join(c for c, _ in a["motivos"]),
            "avisos_api": " | ".join(a["avisos"]), "dictamen": " | ".join(a["dictamen"]),
        })
        for r in a["registros"]:
            ivas.append({
                "documento": e["origen"], "trabajo": e["trabajo"], "direccion": a["direccion"],
                "numero": a["numero"], "fecha": a["fecha"], "contraparte_nif": a["contraparte"]["nif"],
                "clase": r["clase"], "tipo_iva": tipo(r["tipo_iva"]), "base": imp(r["base"]),
                "cuota": imp(r["cuota"]), "tipo_recargo": tipo(r["tipo_recargo"]),
                "cuota_recargo": imp(r["cuota_recargo"]), "origen": r["origen"],
            })
    return facturas, ivas, omitidos


def escribir(ruta, columnas, filas_, bom=False):
    with open(ruta, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(columnas)
        for f in filas_:
            w.writerow([v1.celda_segura(f.get(c, "")) for c in columnas])


def main(argv=None):
    p = argparse.ArgumentParser(description="Respuesta v1 de MuninCloud -> facturas.csv + iva.csv")
    p.add_argument("entradas", nargs="+", help="ficheros .json o directorios con las respuestas de la API")
    p.add_argument("--salida", required=True, help="directorio donde escribir los CSV")
    p.add_argument("--mi-nif", default="", help="el NIF de tu empresa: comprueba la dirección que da la API (v1.1) y la decide si la "
                        "respuesta no la trae")
    p.add_argument("--direccion", choices=["RECIBIDA", "EMITIDA"], default="",
                   help="fuerza la dirección de TODAS las facturas (manda sobre la que da la API)")
    p.add_argument("--excel-es", action="store_true", help="coma decimal y marca UTF-8, para Excel en español")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION} (lectura v1 {v1.VERSION_V1})")
    args = p.parse_args(argv)

    entradas = v1.leer_entradas(args.entradas)
    facturas, ivas, omitidos = filas(entradas, args.mi_nif, args.direccion, args.excel_es)
    os.makedirs(args.salida, exist_ok=True)
    escribir(os.path.join(args.salida, "facturas.csv"), COLUMNAS_FACTURAS, facturas, args.excel_es)
    escribir(os.path.join(args.salida, "iva.csv"), COLUMNAS_IVA, ivas, args.excel_es)
    escribir(os.path.join(args.salida, "omitidos.csv"), COLUMNAS_OMITIDOS, omitidos, args.excel_es)
    revisar = sum(1 for f in facturas if f["revisar"] == "SI")
    print(f"facturas={len(facturas)} filas_iva={len(ivas)} para_revisar={revisar} omitidos={len(omitidos)} "
          f"salida={args.salida}")
    for f in facturas:
        if f["revisar"] == "SI":
            print(f"  revisar  {f['documento']}: {f['motivos_revision']}")
    for o in omitidos:
        print(f"  omitido  {o['documento']}: {o['motivo']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

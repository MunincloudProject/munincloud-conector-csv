#!/usr/bin/env python3
"""VÍA B del conector csv · comprueba los ficheros YA GENERADOS (facturas.csv, iva.csv, omitidos.csv) sin importar
su código: lee las respuestas v1 con su propio parser, rehace las cuentas en céntimos enteros y coteja fila a fila.

    python3 pruebas/via_b.py <directorio de salida> <directorio de entrada> [--mi-nif NIF]

Portado del comprobador con el que se verificó el formato de salida (la parte del CSV genérico) al contrato del kit
de conectores de MuninCloud. No comparte una línea con `munincloud_csv.py` ni con `munincloud_v1.py`.
Imprime una línea VEREDICTO por documento, una FALLO por regla rota y al final VIA_B=OK | VIA_B=FALLOS:n | VIA_B=NO_MEDIBLE.
"""
import argparse
import csv
import json
import os
import re
import sys

REDONDEO = 2                                   # céntimos entre líneas y cabecera que se funden
TOL = 2  # céntimos
LEGALES_X100 = {0, 200, 400, 500, 700, 750, 950, 1000, 1500, 2100}           # % IVA x 100
RE_X100 = {2100: 520, 1000: 140, 400: 50}                                     # art. 161 LIVA
CAB_FACT = ("documento trabajo direccion numero fecha divisa emisor_nombre emisor_nif receptor_nombre receptor_nif "
            "contraparte_nombre contraparte_nif contraparte_pais es_rectificativa rectifica_a_numero "
            "rectifica_a_fecha base_imponible cuota_iva suplidos_no_sujetos recargo_equivalencia retencion "
            "porcentaje_retencion total_factura descuadre cuadra estado_nif_aeat revisar motivos_revision "
            "avisos_api dictamen").split()
CAB_IVA = "documento trabajo direccion numero fecha contraparte_nif clase tipo_iva base cuota tipo_recargo " \
          "cuota_recargo origen".split()
IMPORTE = re.compile(r"^-?\d+\.\d\d$")
# v1.3 (contrato, verificacion_identidad.situacion): las que el README del csv manda a revisar.
SITUACIONES_GRAVES = {"IDENTIFICADO-BAJA", "IDENTIFICADO-REVOCADO", "NO IDENTIFICADO"}

fallos = []


def fallo(donde, que):
    fallos.append(f"FALLO {donde}: {que}")


# ─── aritmética en céntimos ───────────────────────────────────────────────────

def cent(x):
    """Un número JSON o un texto '12.34' -> céntimos (int), redondeo comercial. None si no hay número."""
    if x is None or x == "" or isinstance(x, bool):
        return None
    s = str(x).strip().replace(",", ".")
    m = re.match(r"^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$", s)
    if not m:
        return None
    if m.group(4):                                   # notación científica: rara, se pasa por repr
        s = format(float(s), ".6f")
        m = re.match(r"^(-?)(\d+)(?:\.(\d+))?$", s)
    signo, ent, dec = m.group(1), m.group(2), (m.group(3) or "")
    dec = (dec + "000")[:3]
    milesimas = int(ent) * 1000 + int(dec)
    c = milesimas // 10 + (1 if milesimas % 10 >= 5 else 0)
    return -c if signo else c


def pct100(x):
    """Un porcentaje -> centésimas de punto (21 -> 2100, 5.2 -> 520)."""
    return cent(x)


def cuota_linea(base_c, tipo_c):
    """round(base x tipo / 100) en céntimos, redondeo comercial con signo."""
    num = abs(base_c) * abs(tipo_c)
    q, r = divmod(num, 10000)
    q += 1 if r * 2 >= 10000 else 0
    return -q if (base_c < 0) != (tipo_c < 0) and q else q


# ─── la entrada, leída por su cuenta ──────────────────────────────────────────

def sin_centinela(v):
    t = "" if v is None else str(v).strip()
    u = t.upper()
    if u in ("S/N", "NO_VISIBLE") or (u.startswith(("NO ", "NO_")) and u.endswith("VISIBLE")):
        return ""
    return t


def nif(v):
    t = "".join(ch for ch in sin_centinela(v).upper() if ch.isalnum())
    if len(t) == 11 and t[:2] == "ES" and t[2].isalnum() and t[3:10].isdigit():
        t = t[2:]
    return t


def fecha_ok(t):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)$", t or "")
    if not m:
        return False
    y, mo, d = map(int, m.groups())
    if not 1 <= mo <= 12:
        return False
    bis = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    return 1 <= d <= [31, 29 if bis else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]


def esperado(doc, mi):
    """Lo que tendría que decir cualquier conector de esta factura: dirección, cifras y semáforo.

    Desde v1.1: la dirección es `documento.direccion` si viene; la del NIF solo la sustituye si no viene, y si las
    dos existen y se contradicen la factura es ROJA (`direccion_contradice`). El recargo es `factura.recargo_total` si
    viene (número); si no, el calculado por la marca `tiene_recargo_equivalencia`. El que viene se reparte en un único
    tipo de IVA cuyo porcentaje legal lo dé; si no se puede, ROJA (`recargo_sin_desglose`)."""
    f = doc.get("factura") or {}
    prov, cli, papel = doc.get("proveedor") or {}, doc.get("cliente") or {}, doc.get("lectura_papel") or {}
    em = {x for x in (nif(prov.get("cif_nif")), nif(papel.get("emisor"))) if x}
    re_ = {x for x in (nif(cli.get("cif_nif")), nif(papel.get("receptor"))) if x}
    if mi in em and mi not in re_:
        por_nif = "EMITIDA"
    elif mi in re_ and mi not in em:
        por_nif = "RECIBIDA"
    else:
        por_nif = "DESCONOCIDA"
    dada = doc.get("direccion") if doc.get("direccion") in ("RECIBIDA", "EMITIDA") else None
    d = dada or por_nif
    contradice = bool(dada and mi and por_nif != "DESCONOCIDA" and por_nif != dada)
    otra = prov if d == "RECIBIDA" else cli if d == "EMITIDA" else {}
    lineas = doc.get("lineas") or []
    base, iva, total = cent(f.get("base_total")), cent(f.get("iva_total")), cent(f.get("total_factura"))
    sup = sum(cent(l.get("amount")) or 0 for l in lineas if l.get("es_no_sujeto"))
    base_l = sum(cent(l.get("amount")) or 0 for l in lineas if not l.get("es_no_sujeto"))
    iva_l = sum(cuota_linea(cent(l.get("amount")) or 0, pct100(l.get("tax_percent")) or 0)
                for l in lineas if not l.get("es_no_sujeto"))
    por_tipo = {}
    for l in lineas:
        if not l.get("es_no_sujeto"):
            t = pct100(l.get("tax_percent")) or 0
            por_tipo[t] = por_tipo.get(t, 0) + (cent(l.get("amount")) or 0)
    re_c = 0
    if f.get("tiene_recargo_equivalencia"):
        for t, b in por_tipo.items():
            if t in RE_X100:
                re_c += cuota_linea(b, RE_X100[t])
    rt = f.get("recargo_total")
    rt = cent(rt) if (rt is not None and not isinstance(rt, bool)) else None
    re_desglose = re_c                                # lo que llevan las filas de IVA
    if rt is not None and not f.get("tiene_recargo_equivalencia"):
        tipos = dict(por_tipo)
        if not lineas and base:                       # sin líneas: el tipo que explica cuota/base (como el conector)
            tipos = {t: base for t in LEGALES_X100 if iva is not None and abs(iva * 10000 / base - t) < 50}
        if len(tipos) == 1:
            (t, b), = tipos.items()
            if t in RE_X100 and abs(cuota_linea(b, RE_X100[t]) - rt) < TOL:
                re_desglose = rt
    re_total = rt if rt is not None else re_c         # el de la ecuación
    ret, ret_ok = 0, True
    for i in doc.get("impuestos_y_retenciones") or []:
        r, imp = cent(i.get("rate")), cent(i.get("importe"))
        if (r is not None and r < 0) or (imp is not None and imp < 0):
            if imp is None:
                ret_ok = False
            else:
                ret += abs(imp)
    rojo = []
    if d == "DESCONOCIDA":
        rojo.append("direccion")
    if contradice:
        rojo.append("direccion_contradice")
    if rt is not None and abs(rt - re_desglose) >= TOL:
        rojo.append("recargo_sin_desglose")
    if not sin_centinela(f.get("numero")):
        rojo.append("numero")
    if not fecha_ok(sin_centinela(f.get("fecha"))):
        rojo.append("fecha")
    if d != "DESCONOCIDA" and not nif(otra.get("cif_nif")):
        rojo.append("nif")
    descuadre = None
    if None in (base, iva, total) or not ret_ok:
        rojo.append("importes")
    else:
        descuadre = base + iva + sup + re_total - ret - total
        if abs(descuadre) >= TOL:
            rojo.append("descuadre")
        if base == 0 and iva == 0 and total == 0 and not lineas:
            rojo.append("sin_importes")
    # 1 o 2 céntimos entre líneas y cabecera son redondeo (el conector los funde y la factura sigue); 3 o más, ROJA
    if lineas and (abs(base_l - (base or 0)) > REDONDEO or abs(iva_l - (iva or 0)) > REDONDEO):
        rojo.append("desglose")
    if any(t not in LEGALES_X100 for t in por_tipo):
        rojo.append("tipo")
    if not lineas and base and iva is not None:
        tipo_ef = iva * 10000 / base
        if not any(abs(tipo_ef - t) < 50 for t in LEGALES_X100):
            rojo.append("tipo")
    divisa = sin_centinela(f.get("divisa")).upper()
    if divisa and divisa != "EUR":                     # v1.2: los importes no se convierten
        rojo.append("divisa")
    situacion = " ".join(str((doc.get("verificacion_identidad") or {}).get("situacion") or "").upper().split())
    if situacion in SITUACIONES_GRAVES:                # v1.3: la AEAT da el NIF de la otra parte por malo
        rojo.append("nif_aeat")
    if doc.get("avisos"):
        rojo.append("avisos")
    if any(h.get("gravedad") in ("rechazado", "aviso") for h in doc.get("dictamen_auditoria") or []):
        rojo.append("dictamen")
    return {"direccion": d, "numero": sin_centinela(f.get("numero")), "fecha": sin_centinela(f.get("fecha")),
            "nif_otra": nif(otra.get("cif_nif")), "nombre_otra": sin_centinela(otra.get("nombre")),
            "base": base, "iva": iva, "total": total, "suplidos": sup, "re": re_desglose, "ret": ret,
            "re_total": re_total if (rt is not None or f.get("tiene_recargo_equivalencia")) else None,
            "descuadre": descuadre, "rojo": rojo, "rect": bool(f.get("es_rectificativa")),
            "divisa": divisa, "situacion": situacion}


def marcar_repetidas(esp):
    """la misma factura dos veces en el lote (dirección, NIF de la otra parte, número sin signos y fecha, en
    orden de nombre): la primera sigue y las demás son ROJAS (`duplicada`). Sin los cuatro datos no se compara."""
    vistas = {}
    for k in sorted(esp):
        e = esp[k]
        clave = (e["direccion"], e["nif_otra"], "".join(c for c in e["numero"].upper() if c.isascii() and c.isalnum()),
                 e["fecha"] if fecha_ok(e["fecha"]) else "")
        if e["direccion"] == "DESCONOCIDA" or not all(clave):
            continue
        if clave in vistas:
            e["rojo"].append("duplicada")
        else:
            vistas[clave] = k


def leer_entradas(directorio):
    docs, omit = {}, set()
    for nombre in sorted(os.listdir(directorio)):
        if not nombre.lower().endswith(".json"):
            continue
        with open(os.path.join(directorio, nombre), encoding="utf-8") as fh:
            o = json.load(fh)
        if isinstance(o, dict) and o.get("estado") == "terminado" and isinstance(o.get("documento"), dict):
            docs[nombre] = o["documento"]
        elif isinstance(o, dict) and isinstance(o.get("factura"), dict) and isinstance(o.get("lineas"), list):
            docs[nombre] = o
        else:
            omit.add(nombre)
    return docs, omit


def leer_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        filas = list(csv.reader(fh, delimiter=";"))
    return filas[0], filas[1:]


# ─── csv_generico ─────────────────────────────────────────────────────────────

def comprobar_csv(dir_csv, esp, omit):
    cab, filas = leer_csv(os.path.join(dir_csv, "facturas.csv"))
    if cab != CAB_FACT:
        fallo("facturas.csv", f"cabecera distinta de la documentada ({len(cab)} columnas)")
        return
    vistos = {}
    for n, fila in enumerate(filas, 2):
        r = dict(zip(cab, fila))
        doc = r["documento"]
        if doc in vistos:
            fallo("facturas.csv", f"{doc} aparece dos veces")
        vistos[doc] = r
        e = esp.get(doc)
        if e is None:
            fallo("facturas.csv", f"fila {n}: {doc} no es una factura de la entrada (¿inventada?)")
            continue
        for col in ("base_imponible", "cuota_iva", "suplidos_no_sujetos", "recargo_equivalencia", "retencion",
                    "total_factura", "descuadre"):
            if r[col] and not IMPORTE.match(r[col]):
                fallo("facturas.csv", f"{doc}.{col} «{r[col]}» no es un importe con dos decimales")
        if r["fecha"] and not fecha_ok(r["fecha"]):
            fallo("facturas.csv", f"{doc}.fecha «{r['fecha']}» no es AAAA-MM-DD válida")
        for col in ("cuadra", "revisar", "es_rectificativa"):
            if r[col] not in ("SI", "NO"):
                fallo("facturas.csv", f"{doc}.{col} «{r[col]}» no es SI/NO")
        if r["direccion"] != e["direccion"]:
            fallo("facturas.csv", f"{doc}: dirección {r['direccion']} y la entrada dice {e['direccion']}")
        if r["numero"] != e["numero"]:
            fallo("facturas.csv", f"{doc}: número «{r['numero']}» distinto del leído «{e['numero']}»")
        if r["divisa"].upper() != e["divisa"]:
            fallo("facturas.csv", f"{doc}: divisa «{r['divisa']}» ≠ entrada «{e['divisa']}»")
        if " ".join(r["estado_nif_aeat"].upper().split()) != e["situacion"]:
            fallo("facturas.csv", f"{doc}: estado_nif_aeat «{r['estado_nif_aeat']}» ≠ entrada «{e['situacion']}»")
        if r["contraparte_nif"] != e["nif_otra"]:
            fallo("facturas.csv", f"{doc}: NIF de la contraparte «{r['contraparte_nif']}» ≠ «{e['nif_otra']}»")
        if r["contraparte_nombre"] not in ("", e["nombre_otra"]):
            fallo("facturas.csv", f"{doc}: nombre de la contraparte no sale de la entrada")
        for col, k in (("base_imponible", "base"), ("cuota_iva", "iva"), ("total_factura", "total"),
                       ("suplidos_no_sujetos", "suplidos"), ("retencion", "ret")):
            if cent(r[col]) != e[k]:
                fallo("facturas.csv", f"{doc}.{col} {r[col]} ≠ entrada {e[k]} céntimos")
        if e["descuadre"] is not None and cent(r["descuadre"]) != e["descuadre"]:
            fallo("facturas.csv", f"{doc}: descuadre {r['descuadre']} ≠ {e['descuadre']} céntimos")
        if cent(r["recargo_equivalencia"]) != e["re_total"]:     # vacío = None en las dos
            fallo("facturas.csv", f"{doc}: recargo {r['recargo_equivalencia'] or 'vacío'} ≠ {e['re_total']} céntimos")
        if (r["revisar"] == "SI") != bool(e["rojo"]):
            fallo("facturas.csv", f"{doc}: revisar={r['revisar']} y la vía B lo ve {'ROJO' if e['rojo'] else 'VERDE'} "
                                  f"({','.join(e['rojo'])})")
    for doc in esp:
        if doc not in vistos:
            fallo("facturas.csv", f"{doc} falta (factura perdida)")
    _, om = leer_csv(os.path.join(dir_csv, "omitidos.csv"))
    if {f[0] for f in om} != omit:
        fallo("omitidos.csv", f"omitidos {sorted(f[0] for f in om)} ≠ entrada {sorted(omit)}")

    cab, filas = leer_csv(os.path.join(dir_csv, "iva.csv"))
    if cab != CAB_IVA:
        fallo("iva.csv", "cabecera distinta de la documentada")
        return
    sumas = {}
    for fila in filas:
        r = dict(zip(cab, fila))
        s = sumas.setdefault(r["documento"], {"base": 0, "cuota": 0, "sup": 0, "re": 0})
        if not IMPORTE.match(r["base"]) or not IMPORTE.match(r["cuota"]):
            fallo("iva.csv", f"{r['documento']}: importe sin formato")
            continue
        if r["clase"] == "NO_SUJETO":
            s["sup"] += cent(r["base"])
        elif r["clase"] in ("IVA", "AJUSTE"):
            s["base"] += cent(r["base"])
            s["cuota"] += cent(r["cuota"])
        else:
            fallo("iva.csv", f"{r['documento']}: clase «{r['clase']}» desconocida")
        if r["clase"] == "IVA" and r["tipo_iva"] != "" and pct100(r["tipo_iva"]) not in LEGALES_X100:
            if "tipo" not in esp.get(r["documento"], {}).get("rojo", []):
                fallo("iva.csv", f"{r['documento']}: tipo {r['tipo_iva']} no legal sin que la factura vaya a revisar")
        s["re"] += cent(r["cuota_recargo"]) or 0
    for doc, e in esp.items():
        s = sumas.get(doc, {"base": 0, "cuota": 0, "sup": 0, "re": 0})
        if (s["base"], s["cuota"], s["sup"]) != (e["base"] or 0, e["iva"] or 0, e["suplidos"]):
            fallo("iva.csv", f"{doc}: filas de IVA suman base {s['base']} cuota {s['cuota']} suplidos {s['sup']}, "
                             f"la cabecera {e['base']} {e['iva']} {e['suplidos']} (céntimos)")
        if s["re"] != e["re"]:
            fallo("iva.csv", f"{doc}: recargo {s['re']} ≠ {e['re']} céntimos")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("salida", help="directorio con lo que escribió el conector")
    p.add_argument("entrada", help="directorio con las respuestas v1")
    p.add_argument("--mi-nif", default="B00000018")
    a = p.parse_args(argv)
    if not os.path.isdir(a.entrada) or not os.path.isdir(a.salida):
        print("VIA_B=NO_MEDIBLE motivo=falta un directorio")
        return 2
    docs, omit = leer_entradas(a.entrada)
    mi = nif(a.mi_nif)
    esp = {k: esperado(d, mi) for k, d in docs.items()}
    marcar_repetidas(esp)
    try:
        comprobar_csv(a.salida, esp, omit)
    except (OSError, ValueError, KeyError) as e:
        print(f"VIA_B=NO_MEDIBLE motivo={type(e).__name__}: {e}")
        return 2
    for k in sorted(set(esp) | omit):
        if k in omit:
            print(f"VEREDICTO {k} OMITIDO")
        else:
            e = esp[k]
            print(f"VEREDICTO {k} {'ROJO' if e['rojo'] else 'VERDE'} descuadre={e['descuadre']} "
                  f"motivos={','.join(e['rojo']) or '-'}")
    for f in fallos:
        print(f)
    verdes = sum(1 for e in esp.values() if not e["rojo"])
    estado = "OK" if not fallos else f"FALLOS:{len(fallos)}"
    print(f"VIA_B={estado} facturas={len(esp)} verdes={verdes} rojos={len(esp) - verdes} omitidos={len(omit)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    sys.exit(main())

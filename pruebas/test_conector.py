"""Pruebas del conector CSV genérico. Solo biblioteca estándar:

    python3 -m unittest discover -s pruebas -v
"""
import contextlib
import csv
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import munincloud_csv  # noqa: E402
import munincloud_v1 as v1  # noqa: E402

ENTRADA = os.path.join(RAIZ, "ejemplos", "entrada")
SALIDA_REF = os.path.join(RAIZ, "ejemplos", "salida")
DATOS = os.path.join(RAIZ, "pruebas", "datos")
MI_NIF = "B00000018"   # NIF reservado por la AEAT para pruebas


def leer(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as fh:
        filas = list(csv.reader(fh, delimiter=";"))
    return [dict(zip(filas[0], f)) for f in filas[1:]]


_TEMPORALES = []


def tearDownModule():
    for d in _TEMPORALES:
        shutil.rmtree(d, ignore_errors=True)


def generar(entradas, *extra):
    salida = tempfile.mkdtemp(prefix="munincloud_csv_")
    _TEMPORALES.append(salida)
    with contextlib.redirect_stdout(io.StringIO()):
        munincloud_csv.main(["--mi-nif", MI_NIF, "--salida", salida, *extra, *entradas])
    return salida


def c(t):
    return Decimal(t or "0")


class EjemplosDelRepositorio(unittest.TestCase):
    """Las 19 respuestas de ejemplo (v1.1 y v1.3): 15 facturas, un acta que la API rechazó (sus dos ficheros), un
    documento con varias facturas (MULTIFACTURA) y un trabajo en curso."""

    @classmethod
    def setUpClass(cls):
        cls.salida = generar([ENTRADA])
        cls.facturas = leer(os.path.join(cls.salida, "facturas.csv"))
        cls.iva = leer(os.path.join(cls.salida, "iva.csv"))
        cls.omitidos = leer(os.path.join(cls.salida, "omitidos.csv"))

    def test_salida_igual_a_la_del_ejemplo(self):
        for nombre in ("facturas.csv", "iva.csv", "omitidos.csv"):
            with open(os.path.join(self.salida, nombre), "rb") as a, open(os.path.join(SALIDA_REF, nombre), "rb") as b:
                self.assertEqual(hashlib.md5(a.read()).hexdigest(), hashlib.md5(b.read()).hexdigest(), nombre)

    def test_ninguna_entrada_se_pierde(self):
        self.assertEqual(len(self.facturas) + len(self.omitidos), len(os.listdir(ENTRADA)))
        self.assertEqual([o["documento"] for o in self.omitidos],
                         ["en_curso__pendiente.json", "nofactura__recibida.json", "nofactura__solo_clasificar.json",
                          "rechazado__multifactura.json"])

    def test_la_ecuacion_de_cada_factura(self):
        for f in self.facturas:
            ecuacion = (c(f["base_imponible"]) + c(f["cuota_iva"]) + c(f["suplidos_no_sujetos"])
                        + c(f["recargo_equivalencia"]) - c(f["retencion"]) - c(f["total_factura"]))
            self.assertEqual(ecuacion, c(f["descuadre"]), f["documento"])
            self.assertEqual(f["cuadra"] == "SI", abs(ecuacion) < Decimal("0.02"), f["documento"])

    def test_los_registros_de_iva_reconstruyen_la_factura(self):
        for f in self.facturas:
            regs = [r for r in self.iva if r["documento"] == f["documento"]]
            self.assertEqual(sum(c(r["base"]) for r in regs if r["clase"] != "NO_SUJETO"), c(f["base_imponible"]))
            self.assertEqual(sum(c(r["cuota"]) for r in regs), c(f["cuota_iva"]))
            self.assertEqual(sum(c(r["base"]) for r in regs if r["clase"] == "NO_SUJETO"), c(f["suplidos_no_sujetos"]))

    def test_una_fila_por_tipo_de_iva(self):
        mixta = [r for r in self.iva if r["documento"] == "compras__mixta_irpf.json"]
        self.assertEqual([(r["tipo_iva"], r["base"], r["cuota"]) for r in mixta],
                         [("21", "100.00", "21.00"), ("10", "50.00", "5.00")])

    def test_compra_y_venta_no_se_confunden(self):
        por_doc = {f["documento"]: f for f in self.facturas}
        self.assertEqual(por_doc["compras__identidad.json"]["direccion"], "RECIBIDA")
        self.assertEqual(por_doc["compras__identidad.json"]["contraparte_nif"], "B99999997")
        self.assertEqual(por_doc["ventas__intracomunitaria.json"]["direccion"], "EMITIDA")
        self.assertEqual(por_doc["ventas__intracomunitaria.json"]["contraparte_nif"], "DE123456789")
        self.assertEqual(por_doc["ventas__intracomunitaria.json"]["contraparte_pais"], "DE")

    def test_lo_que_no_cuadra_sale_marcado_y_no_se_arregla(self):
        f = {x["documento"]: x for x in self.facturas}["ventas__recargo.json"]
        self.assertEqual((f["cuadra"], f["revisar"], f["descuadre"]), ("NO", "SI", "-10.40"))
        self.assertIn("DESCUADRE", f["motivos_revision"])
        self.assertEqual(f["recargo_equivalencia"], "")      # v1 no lo trae: no se inventa

    def test_la_direccion_es_la_de_la_respuesta(self):
        # v1.1: documento.direccion manda; con el NIF de las pruebas no hay ninguna contradicción
        for f in self.facturas:
            self.assertEqual(f["direccion"], "RECIBIDA" if f["documento"].startswith("compras__") else "EMITIDA")
            self.assertNotIn("DIRECCION_CONTRADICE_NIF", f["motivos_revision"])

    def test_un_dato_no_leido_no_viaja(self):
        f = leer(os.path.join(generar([os.path.join(DATOS, "desafinada__obligatorio.json")]), "facturas.csv"))[0]
        self.assertEqual((f["numero"], f["emisor_nif"], f["revisar"]), ("", "", "SI"))


class Desafinadas(unittest.TestCase):
    """Una cuota que no cuadra y un campo obligatorio vacío: tienen que salir para revisar."""

    def test_cuota_que_no_cuadra(self):
        f = leer(os.path.join(generar([os.path.join(DATOS, "desafinada__cuota.json")]), "facturas.csv"))[0]
        self.assertEqual((f["cuadra"], f["revisar"], f["descuadre"]), ("NO", "SI", "1.00"))
        self.assertIn("DESGLOSE_NO_CUADRA", f["motivos_revision"])

    def test_campo_obligatorio_vacio(self):
        f = leer(os.path.join(generar([os.path.join(DATOS, "desafinada__obligatorio.json")]), "facturas.csv"))[0]
        self.assertEqual(f["revisar"], "SI")
        self.assertIn("NUMERO_NO_LEIDO", f["motivos_revision"])
        self.assertIn("NIF_CONTRAPARTE_NO_LEIDO", f["motivos_revision"])


class RecargoQueViene(unittest.TestCase):
    """v1.1: la misma venta con `factura.recargo_total` (como saldrá el día que el motor lo dé) cuadra."""

    def test_venta_con_recargo_cuadra(self):
        s = generar([os.path.join(DATOS, "venta_con_recargo.json")])
        f = leer(os.path.join(s, "facturas.csv"))[0]
        self.assertEqual((f["recargo_equivalencia"], f["descuadre"], f["cuadra"]), ("10.40", "0.00", "SI"))
        self.assertNotIn("DESCUADRE", f["motivos_revision"])
        r = [x for x in leer(os.path.join(s, "iva.csv")) if x["clase"] == "IVA"]
        self.assertEqual([(x["tipo_iva"], x["tipo_recargo"], x["cuota_recargo"]) for x in r], [("21", "5.2", "10.40")])


class PiezaV1(unittest.TestCase):

    def test_nif(self):
        self.assertEqual(v1.normalizar_nif(" esb-00.000.018 "), "B00000018")
        self.assertEqual(v1.normalizar_nif("DE123456789"), "DE123456789")
        self.assertEqual(v1.normalizar_nif("NO_VISIBLE"), "")

    def test_fecha(self):
        self.assertEqual(v1.fecha_valida("2024-02-29"), "2024-02-29")
        self.assertEqual(v1.fecha_valida("2026-02-29"), "")
        self.assertEqual(v1.fecha_valida("2021-01-41"), "")

    def test_celda_que_no_ejecuta(self):
        self.assertEqual(v1.celda_segura('=HYPERLINK("http://x","pulse")'), '\'=HYPERLINK("http://x","pulse")')
        self.assertEqual(v1.celda_segura("-12.50"), "-12.50")     # un abono sigue siendo un número

    def test_sin_mi_nif_la_direccion_es_desconocida(self):
        doc = {"factura": {"numero": "1"}, "lineas": [], "proveedor": {"cif_nif": "B99999997"},
               "cliente": {"cif_nif": "B00000018"}}
        self.assertEqual(v1.direccion(doc), v1.DESCONOCIDA)
        self.assertEqual(v1.direccion(doc, "B00000018"), v1.RECIBIDA)
        self.assertEqual(v1.direccion(doc, "A00000000"), v1.DESCONOCIDA)

    def test_la_direccion_de_la_respuesta_manda_y_se_comprueba(self):
        doc = {"direccion": "EMITIDA", "factura": {"numero": "1", "fecha": "2026-09-25", "base_total": 100,
                                                   "iva_total": 21, "total_factura": 121, "recargo_total": None},
               "lineas": [{"amount": 100, "tax_percent": 21}],
               "proveedor": {"cif_nif": "B99999997"}, "cliente": {"cif_nif": "B00000018"}}
        self.assertEqual(v1.direccion(doc), v1.EMITIDA)                       # sin NIF: la de la respuesta
        a = v1.analizar(doc, MI_NIF)                                            # mi NIF solo en el lado receptor
        self.assertEqual(a["direccion"], v1.EMITIDA)
        self.assertIn("DIRECCION_CONTRADICE_NIF", [c for c, _ in a["motivos"]])
        self.assertEqual(v1.analizar(doc, MI_NIF, "RECIBIDA")["motivos"], [])   # forzada: manda y no se discute

    def test_recargo_que_viene_sin_desglose_posible(self):
        doc = {"direccion": "EMITIDA",
               "factura": {"numero": "R-2", "fecha": "2026-09-25", "base_total": 200, "iva_total": 31,
                           "total_factura": 237.2, "recargo_total": 6.2},
               "lineas": [{"amount": 100, "tax_percent": 21}, {"amount": 100, "tax_percent": 10}],
               "proveedor": {"cif_nif": "B00000018"}, "cliente": {"cif_nif": "B99999997"}}
        a = v1.analizar(doc, MI_NIF)
        self.assertEqual((a["recargo"], a["descuadre"]), (Decimal("6.20"), Decimal("0.00")))
        self.assertIn("RECARGO_SIN_DESGLOSE", [c for c, _ in a["motivos"]])    # dos tipos: no se reparte a ojo

    def test_recargo_solo_si_la_factura_lo_declara(self):
        doc = {"factura": {"numero": "R-1", "fecha": "2026-09-25", "base_total": 100, "iva_total": 21,
                           "total_factura": 126.2, "tiene_recargo_equivalencia": True},
               "lineas": [{"amount": 100, "tax_percent": 21}],
               "proveedor": {"cif_nif": "B99999997"}, "cliente": {"cif_nif": "B00000018"}}
        a = v1.analizar(doc, MI_NIF)
        self.assertEqual((a["recargo"], a["descuadre"], a["cuadra"]), (Decimal("5.20"), Decimal("0.00"), True))

    # ─── v1.3, la pieza común ─────────────────────────────────────────────────

    def compra(self, **factura):
        f = {"numero": "C-1", "fecha": "2026-09-25", "divisa": "EUR", "base_total": 100, "iva_total": 21,
             "total_factura": 121, "recargo_total": None}
        f.update(factura)
        return {"direccion": "RECIBIDA", "factura": f, "lineas": [{"amount": 100, "tax_percent": 21}],
                "proveedor": {"cif_nif": "B99999997"}, "cliente": {"cif_nif": MI_NIF}, "avisos": []}

    def codigos(self, doc, nif=MI_NIF):
        return [c for c, _ in v1.analizar(doc, nif)["motivos"]]

    def test_version(self):
        self.assertEqual(v1.VERSION_V1, "1.3.0")

    def test_tipo_discrepante_no_se_cuenta_dos_veces(self):
        doc = self.compra()
        doc["proveedor"], doc["cliente"] = {"cif_nif": MI_NIF}, {"cif_nif": "B99999997"}   # yo, del lado emisor
        self.assertEqual(self.codigos(doc), ["DIRECCION_CONTRADICE_NIF"])                  # v1.1/v1.2: sin el aviso
        doc["avisos"] = [{"codigo": "TIPO_DISCREPANTE", "mensaje": "se declaró compra"}]
        self.assertEqual(self.codigos(doc), ["AVISO_TIPO_DISCREPANTE"])                    # v1.3: solo el aviso

    def test_nif_de_baja_o_revocado_sale_para_revisar(self):
        for situacion, sale in (("IDENTIFICADO-REVOCADO", True), ("IDENTIFICADO-BAJA", True),
                                ("IDENTIFICADO", False), ("", False)):
            doc = self.compra()
            doc["verificacion_identidad"] = {"situacion": situacion}
            self.assertEqual("NIF_AEAT_BAJA_O_REVOCADO" in self.codigos(doc), sale, situacion)

    def test_nif_no_identificado_sale_con_su_motivo(self):
        for situacion, codigos in (("NO IDENTIFICADO", ["NIF_AEAT_NO_IDENTIFICADO"]),
                                   ("no identificado", ["NIF_AEAT_NO_IDENTIFICADO"]), ("IDENTIFICADO", [])):
            doc = self.compra()
            doc["verificacion_identidad"] = {"situacion": situacion}
            self.assertEqual(self.codigos(doc), codigos, situacion)

    def test_descuadre_avisado_no_se_cuenta_dos_veces(self):
        doc = self.compra(total_factura=131)
        self.assertEqual(self.codigos(doc), ["DESCUADRE"])                                 # sin el aviso: el propio
        doc["avisos"] = [{"codigo": "DESCUADRE", "mensaje": "no coincide con el total"}]
        a = v1.analizar(doc, MI_NIF)
        self.assertEqual([c for c, _ in a["motivos"]], ["AVISO_DESCUADRE"])               # con el aviso: solo el suyo
        self.assertEqual((a["descuadre"], a["cuadra"]), (Decimal("-10.00"), False))       # la cifra, igual

    def test_divisa_que_no_es_euro_sale_para_revisar(self):
        self.assertEqual(self.codigos(self.compra(divisa="USD")), ["DIVISA_NO_EUR"])
        self.assertEqual(self.codigos(self.compra(divisa="eur")), [])
        self.assertEqual(self.codigos(self.compra(divisa=None)), [])          # no viene: no se supone otra

    def test_linea_sin_importe_no_se_calcula(self):
        doc = self.compra()
        doc["lineas"] = [{"qty": 2, "rate": 50, "tax_percent": 21}]          # sin amount: no se hace qty x rate
        self.assertIn("DESGLOSE_NO_CUADRA", self.codigos(doc))

    def test_clave_de_operacion_es_una_ayuda_aparte(self):
        doc = self.compra(base_total=420, iva_total=0, total_factura=420)
        doc["proveedor"] = {"cif_nif": "FR40123456824"}
        doc["lineas"] = [{"descripcion_original": "Portes", "amount": 20, "tax_percent": 0},
                         {"descripcion_original": "Bomba dosificadora", "amount": 400, "tax_percent": 0}]
        k = v1.clave_operacion(doc, MI_NIF)
        self.assertEqual((k["clave"], k["cuenta"]), (v1.ADQUISICION_INTRACOMUNITARIA_BIENES, "601"))
        self.assertEqual(self.codigos(doc), [])                              # analizar no la usa
        doc["proveedor"] = {"cif_nif": "CHE-123.456.789"}
        doc["lineas"] = [{"descripcion_original": "Reparación de la bomba", "amount": 420, "tax_percent": 0}]
        self.assertEqual(v1.clave_operacion(doc, MI_NIF)["clave"], v1.SERVICIO_EXTRACOMUNITARIO_ISP)
        doc["lineas"] = [{"descripcion_original": "Pedido 4711", "amount": 420, "tax_percent": 0}]
        self.assertIsNone(v1.clave_operacion(doc, MI_NIF)["clave"])          # el concepto no decide: no se adivina
        self.assertIsNone(v1.clave_operacion(self.compra(), MI_NIF)["clave"])   # proveedor español
        doc = self.compra(iva_total=21)
        doc["proveedor"] = {"cif_nif": "FR40123456824"}
        self.assertIsNone(v1.clave_operacion(doc, MI_NIF)["clave"])          # repercute IVA español
        doc["direccion"] = "EMITIDA"
        self.assertIsNone(v1.clave_operacion(doc)["clave"])                  # solo compras

    def test_maestro_de_terceros(self):
        d = tempfile.mkdtemp(prefix="munincloud_maestro_")
        _TEMPORALES.append(d)
        ruta = os.path.join(d, "maestro.csv")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("NIF;id\nESB-99999997;__export__.res_partner_12\n# comentario\n\nFR40123456824;400000007\n")
        self.assertEqual(v1.leer_maestro(ruta), {"B99999997": "__export__.res_partner_12", "FR40123456824": "400000007"})
        with self.assertRaises(ValueError):
            v1.leer_maestro(ruta, formato=re.compile(r"^[1-9][0-9]*$"))     # un id de Odoo no es una subcuenta
        self.assertEqual(v1.leer_maestro(""), {})
        self.assertEqual((v1.ajustar_longitud("629", 8), v1.ajustar_longitud("4000000001", 8)), ("62900000", "4000000001"))
        self.assertEqual([v1.prefijo_contraparte(x) for x in (v1.RECIBIDA, v1.EMITIDA, v1.DESCONOCIDA)], ["400", "430", ""])

def caso(nombre, cambiar, como=None):
    """Una respuesta de los ejemplos, con `cambiar(documento)`, en un .json temporal (con el nombre `como`)."""
    import json
    with open(os.path.join(ENTRADA, nombre), encoding="utf-8") as fh:
        obj = json.load(fh)
    cambiar(obj["documento"])
    d = tempfile.mkdtemp(prefix="munincloud_csv_caso_")
    _TEMPORALES.append(d)
    ruta = os.path.join(d, como or nombre)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return ruta


class DuplicadasYRedondeo(unittest.TestCase):
    """La misma factura dos veces en el lote, y 1-2 céntimos de redondeo entre líneas y cabecera, con sus contrarios."""

    def filas(self, *rutas):
        s = generar(list(rutas))
        return ({f["documento"]: f for f in leer(os.path.join(s, "facturas.csv"))}, leer(os.path.join(s, "iva.csv")))

    def test_la_segunda_duplicada_a_revisar_y_la_primera_no(self):
        fac, _ = self.filas(*(os.path.join(ENTRADA, n) for n in ("compras__duplicada_a.json", "compras__duplicada_b.json")))
        self.assertEqual((fac["compras__duplicada_a.json"]["revisar"], fac["compras__duplicada_a.json"]["motivos_revision"]),
                         ("NO", ""))
        self.assertEqual(fac["compras__duplicada_b.json"]["revisar"], "SI")
        self.assertIn("POSIBLE_DUPLICADA", fac["compras__duplicada_b.json"]["motivos_revision"])

    def test_otro_numero_no_es_duplicada(self):
        otra = caso("compras__duplicada_b.json", lambda d: d["factura"].update(numero="P-2026-3009"))
        fac, _ = self.filas(os.path.join(ENTRADA, "compras__duplicada_a.json"), otra)
        self.assertEqual(sorted(f["revisar"] for f in fac.values()), ["NO", "NO"])

    def test_el_numero_se_compara_sin_signos(self):
        otra = caso("compras__duplicada_b.json", lambda d: d["factura"].update(numero="p 2026/3001"))
        fac, _ = self.filas(os.path.join(ENTRADA, "compras__duplicada_a.json"), otra)
        self.assertIn("POSIBLE_DUPLICADA", fac["compras__duplicada_b.json"]["motivos_revision"])

    def test_uno_y_dos_centimos_cuadran_exacto_sin_fila_ajuste_y_sin_revisar(self):
        dos = caso("compras__redondeo_base.json", lambda d: d["factura"].update(base_total=76.55, total_factura=92.63, numero="P-2026-3005"),
                   "compras__redondeo_dos.json")
        fac, iva = self.filas(os.path.join(ENTRADA, "compras__redondeo_base.json"),
                              os.path.join(ENTRADA, "compras__redondeo_cuota.json"), dos)
        for doc, base, cuota in (("compras__redondeo_base.json", "76.54", "16.08"),
                                 ("compras__redondeo_cuota.json", "76.53", "16.07"),
                                 ("compras__redondeo_dos.json", "76.55", "16.08")):
            self.assertEqual((fac[doc]["revisar"], fac[doc]["base_imponible"], fac[doc]["cuota_iva"]), ("NO", base, cuota))
            regs = [r for r in iva if r["documento"] == doc]
            self.assertEqual([(r["clase"], r["base"], r["cuota"]) for r in regs], [("IVA", base, cuota)], doc)

    def test_tres_centimos_a_revisar_con_fila_ajuste(self):
        fac, iva = self.filas(os.path.join(ENTRADA, "compras__descuadre_3c.json"))
        f = fac["compras__descuadre_3c.json"]
        self.assertEqual(f["revisar"], "SI")
        self.assertIn("DESGLOSE_NO_CUADRA", f["motivos_revision"])
        self.assertIn(("AJUSTE", "0.03"), [(r["clase"], r["base"]) for r in iva])

    def test_la_pieza_apunta_el_ajuste_como_nota_y_no_como_motivo(self):
        import json
        with open(os.path.join(ENTRADA, "compras__redondeo_cuota.json"), encoding="utf-8") as fh:
            a = v1.analizar(json.load(fh)["documento"], MI_NIF)
        self.assertFalse(a["revisar"])
        self.assertEqual([c for c, _ in a["notas"]], ["AJUSTE_REDONDEO"])


if __name__ == "__main__":
    unittest.main()

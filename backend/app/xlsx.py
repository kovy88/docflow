"""Small, dependency-free OOXML export for the pilot's flat evidence table.

Text is always an inline string, never a formula (including values beginning with =).
Dates and amounts remain native Excel values. No document content enters XML markup.
"""

import io
import math
import re
from datetime import date, datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def xml(node: Element) -> bytes:
    return tostring(node, encoding="utf-8", xml_declaration=True)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def workbook(headers: list[str], rows: list[list[object]]) -> bytes:
    sheet = Element("worksheet", xmlns=NS)
    views = SubElement(sheet, "sheetViews")
    view = SubElement(views, "sheetView", workbookViewId="0")
    SubElement(view, "pane", ySplit="1", topLeftCell="A2", state="frozen", activePane="bottomLeft")
    cols = SubElement(sheet, "cols")
    for index in range(1, len(headers) + 1):
        SubElement(cols, "col", min=str(index), max=str(index), width="28", customWidth="1")
    data = SubElement(sheet, "sheetData")
    for row_index, values in enumerate([headers, *rows], 1):
        row = SubElement(data, "row", r=str(row_index))
        for col_index, value in enumerate(values, 1):
            attrs = {"r": f"{column_name(col_index)}{row_index}"}
            if value is None:
                continue
            if row_index == 1:
                attrs["s"] = "1"
            if isinstance(value, (date, datetime)):
                # Dates in this export are modern; Excel's 1900 leap-day quirk is included.
                value = (value.date() if isinstance(value, datetime) else value) - date(
                    1899, 12, 30
                )
                cell = SubElement(row, "c", attrs | {"s": "2"})
                SubElement(cell, "v").text = str(value.days)
            elif isinstance(value, (int, float)) and math.isfinite(value):
                cell = SubElement(row, "c", attrs | {"s": "3"})
                SubElement(cell, "v").text = str(value)
            else:
                cell = SubElement(row, "c", attrs | {"t": "inlineStr"})
                text = SubElement(SubElement(cell, "is"), "t", {"xml:space": "preserve"})
                text.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))[:32767]
    SubElement(sheet, "autoFilter", ref=f"A1:{column_name(len(headers))}{len(rows) + 1}")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            f'''<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Evidence" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/styles.xml",
            f'''<styleSheet xmlns="{NS}">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="4"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''',
        )
        archive.writestr("xl/worksheets/sheet1.xml", xml(sheet))
    return output.getvalue()

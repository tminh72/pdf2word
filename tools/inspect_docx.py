"""Kiểm tra nhanh cấu trúc file DOCX (debug)."""

from __future__ import annotations

import re
import sys
import zipfile


def main(path: str) -> None:
    z = zipfile.ZipFile(path)
    xml = z.read("word/document.xml").decode("utf-8")
    print("tables:", xml.count("<w:tbl>"))
    print("gridSpan:", len(re.findall(r"gridSpan", xml)))
    print("vMerge:", len(re.findall(r"vMerge", xml)))
    print("tblHeader:", len(re.findall(r"tblHeader", xml)))
    print("media:", [n for n in z.namelist() if n.startswith("word/media")])
    styles = sorted(set(re.findall(r'w:pStyle w:val="([^"]+)"', xml)))
    print("pStyles:", styles)


if __name__ == "__main__":
    main(sys.argv[1])

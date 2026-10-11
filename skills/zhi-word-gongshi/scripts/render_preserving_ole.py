#!/usr/bin/env python3
"""Use the currently resolved documents renderer without importing OLE as math."""
import argparse
import importlib.util
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input_path')
    p.add_argument('--renderer', required=True, help='Current documents skill render_docx.py')
    p.add_argument('--output-dir', required=True)
    args = p.parse_args()
    spec = importlib.util.spec_from_file_location('documents_renderer', Path(args.renderer).resolve())
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    convert = renderer.convert_to_pdf

    def preserving_convert(doc_path, user_profile, convert_tmp_dir, stem, verbose):
        user = Path(user_profile)/'user'
        user.mkdir(parents=True, exist_ok=True)
        (user/'registrymodifications.xcu').write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Filter/Microsoft/Import">'
            '<prop oor:name="MathTypeToMath" oor:op="fuse"><value>false</value></prop>'
            '</item></oor:items>', encoding='utf8')
        return convert(doc_path, user_profile, convert_tmp_dir, stem, verbose)

    renderer.convert_to_pdf = preserving_convert
    source = str(Path(args.input_path).resolve())
    dpi = renderer.calc_dpi_via_ooxml_docx(source, 1600, 2000)
    pages = renderer.rasterize(source, str(Path(args.output_dir).resolve()), dpi, True, True)
    print('Pages rendered:', len(pages), '; target Office/MathType editing NOT tested')


if __name__ == '__main__':
    main()

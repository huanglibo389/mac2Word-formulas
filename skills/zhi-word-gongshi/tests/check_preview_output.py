"""Check renderer output invariants; this is not visual or target-editor QA."""
from pathlib import Path
from PIL import Image
import argparse
import hashlib
import json
import math


def check(manifest_path, png_dir):
    manifest = json.loads(manifest_path.read_text())
    checks = json.loads((manifest_path.parent/'preview_validation.json').read_text())
    completion = json.loads((manifest_path.parent/'render_completion.json').read_text())
    if (completion.get('schema') != 1
            or completion.get('manifest_sha256') != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            or Path(completion.get('png_directory', '')).resolve() != png_dir.resolve()
            or completion.get('previews') != checks):
        raise ValueError('Current manifest, PNG directory and render records are not bound')
    formulas = manifest['formulas']
    indexes = [f['index'] for f in formulas]
    if len(indexes) != len(set(indexes)) or indexes != [r['index'] for r in checks]:
        raise ValueError('Preview checks do not map one-to-one to formulas')
    before = manifest_path.parent/'formulas.before-natural.json'
    if checks and checks[0]['geometry_mode'] == 'natural':
        original = json.loads(before.read_text())
        if original['docx_sha256'] != manifest['docx_sha256'] or original['source_sha256'] != manifest['source_sha256']:
            raise ValueError('Source identity changed')
        if len(original['formulas']) != len(formulas):
            raise ValueError('Formula count changed')
        for old, new in zip(original['formulas'], formulas):
            allowed = {'width_pt', 'height_pt', 'geometry_change'}
            if {k:v for k,v in old.items() if k not in allowed} != {k:v for k,v in new.items() if k not in allowed}:
                raise ValueError('Natural-mode update changed non-geometry data')
    for formula, row in zip(formulas, checks):
        for key in ('width_pt', 'height_pt', 'baseline_from_top_pt', 'descent_pt', 'scale'):
            if not math.isfinite(row[key]):
                raise ValueError('Nonfinite preview metric')
        if not 0 <= row['baseline_from_top_pt'] <= row['height_pt']:
            raise ValueError('Baseline lies outside preview box')
        if abs(row['baseline_from_top_pt']+row['descent_pt']-row['height_pt']) > 1e-6:
            raise ValueError('Baseline and descent do not add up to height')
        if row['geometry_mode'] == 'natural' and row['scale'] != 1:
            raise ValueError('Natural-mode formula was scaled')
        for key in ('width_pt', 'height_pt'):
            if abs(formula[key]-row[key]) > 1e-6:
                raise ValueError('Patch manifest and actual rendered size differ')
        image_path = png_dir/f"equation{formula['index']:04}.png"
        if (row['png_sha256'] != hashlib.sha256(image_path.read_bytes()).hexdigest()
                or row['mathml_sha256'] != hashlib.sha256(formula['mathml'].encode()).hexdigest()
                or row['native_sha256'] != formula['native_sha256']):
            raise ValueError('Preview source/image hash mismatch')
        with Image.open(image_path) as image:
            image.load()
            if image.format != 'PNG' or image.mode != 'RGBA':
                raise ValueError('Expected transparent PNG')
            expected = [row[k]*row['dpi']/72 for k in ('width_pt', 'height_pt')]
            # Locator screenshots quantize fractional boxes to CSS-pixel edges.
            tolerance = 2 if row['geometry_mode'] == 'natural' else max(2, row['dpi']/96+1)
            if any(abs(actual-target) > tolerance for actual,target in zip(image.size, expected)):
                raise ValueError('PNG pixels disagree with recorded dimensions')
            if image.getchannel('A').getbbox() is None:
                raise ValueError('Empty preview image')
    print(f'{len(formulas)} previews: geometry, baseline, PNG and source-identity checks passed. Visual QA still required.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--png-dir', type=Path, required=True)
    args = parser.parse_args()
    check(args.manifest, args.png_dir)

"""Disposable package fixtures for overwrite and stale-preview protection."""
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
from unittest import TestCase, main
from io import BytesIO
from PIL import Image
import importlib.util
import json
import os
import tempfile

spec = importlib.util.spec_from_file_location(
    'legacy_equations', Path(__file__).resolve().parents[1]/'scripts'/'legacy_equations.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class CacheProtection(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='word-cache-safety-')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.docx = root/'input.docx'
        self.source = root/'source.docx'
        self.work = root/'work'
        self.work.mkdir()
        (self.work/'png').mkdir()
        self.output = root/'output.docx'
        xml = (f'<w:document xmlns:w="{legacy.W}" xmlns:v="{legacy.V}" xmlns:o="{legacy.O}" xmlns:r="{legacy.R}"><w:body>')
        relationships = f'<Relationships xmlns="{legacy.REL}">'
        self.formulas = []
        files = {'[Content_Types].xml':f'<Types xmlns="{legacy.CT}"/>'.encode()}
        for i in (1, 2):
            xml += (f'<w:p><w:r><w:object><v:shape style="width:20pt;height:12pt">'
                    f'<v:imagedata r:id="preview{i}"/></v:shape>'
                    f'<o:OLEObject ProgID="Equation.3" r:id="native{i}"/></w:object></w:r></w:p>')
            native_path = f'word/embeddings/native{i}.bin'
            preview_path = f'word/media/old{i}.png'
            relationships += (f'<Relationship Id="native{i}" Target="embeddings/native{i}.bin"/>'
                              f'<Relationship Id="preview{i}" Target="media/old{i}.png"/>')
            native = f'fixture-data-{i}'.encode()
            buffer = BytesIO()
            Image.new('RGBA', (24, 24), (10*i, 0, 0, 255)).save(buffer, format='PNG')
            png = buffer.getvalue()
            files[native_path], files[preview_path] = native, png
            (self.work/'png'/f'equation{i:04}.png').write_bytes(png)
            self.formulas.append(dict(index=i,progid='Equation.3',native_part=native_path,native_sha256=legacy.sha(native),
                preview_part=preview_path,preview_rid=f'preview{i}',mathml=f'<math><mn>{i}</mn></math>',
                width_pt=20,height_pt=12,geometry_change={}))
        files['word/document.xml'] = (xml+'</w:body></w:document>').encode()
        files['word/_rels/document.xml.rels'] = (relationships+'</Relationships>').encode()
        for path in (self.docx, self.source):
            with ZipFile(path, 'x') as package:
                for name,data in files.items():
                    package.writestr(name,data)
        self.manifest = dict(docx=str(self.docx),docx_sha256=legacy.sha(self.docx.read_bytes()),
            source_omml=str(self.source),source_sha256=legacy.sha(self.source.read_bytes()),
            part='word/document.xml',formulas=self.formulas)
        self.save_manifest()
        self.save_completion()
        self.args = SimpleNamespace(work_dir=str(self.work),out=str(self.output),docx=None)

    def save_manifest(self):
        (self.work/'formulas.json').write_text(json.dumps(self.manifest))

    def save_completion(self):
        rows = [dict(index=f['index'],width_pt=f['width_pt'],height_pt=f['height_pt'],
                     native_sha256=f['native_sha256'],mathml_sha256=legacy.sha(f['mathml'].encode()),
                     png_sha256=legacy.sha((self.work/'png'/f"equation{f['index']:04}.png").read_bytes())) for f in self.formulas]
        record = dict(schema=1,manifest_sha256=legacy.sha((self.work/'formulas.json').read_bytes()),
                      png_directory=str((self.work/'png').resolve()),previews=rows)
        (self.work/'render_completion.json').write_text(json.dumps(record))

    def reject_patch(self, text):
        with self.assertRaisesRegex(ValueError, text):
            legacy.patch(self.args)
        self.assertFalse(self.output.exists())

    def test_audit_cannot_overwrite_input(self):
        original = self.docx.read_bytes()
        with self.assertRaisesRegex(ValueError, 'aliases an input'):
            legacy.audit(SimpleNamespace(docx=str(self.docx),report=str(self.docx)))
        self.assertEqual(self.docx.read_bytes(), original)

    def test_audit_cannot_overwrite_hardlink_alias(self):
        alias = self.docx.parent/'alias.json'
        os.link(self.docx, alias)
        original = self.docx.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            legacy.audit(SimpleNamespace(docx=str(self.docx),report=str(alias)))
        self.assertEqual(self.docx.read_bytes(), original)

    def test_prepare_cannot_reuse_previous_work(self):
        original = (self.work/'formulas.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'fresh directory'):
            legacy.prepare(SimpleNamespace(work_dir=str(self.work)))
        self.assertEqual((self.work/'formulas.json').read_bytes(), original)

    def test_completion_is_required(self):
        (self.work/'render_completion.json').unlink()
        self.reject_patch('Missing current render')

    def test_changed_manifest_rejects_old_render(self):
        self.formulas[1]['mathml'] = '<math><mi>q</mi></math>'
        self.save_manifest()
        self.reject_patch('does not bind')

    def test_duplicate_formula_index_is_rejected(self):
        self.formulas[1]['index'] = 1
        self.save_manifest()
        self.reject_patch('indices must be unique')

    def test_zero_geometry_is_rejected(self):
        self.formulas[0].update(width_pt=0,geometry_change={'width_pt':0})
        self.save_manifest()
        self.reject_patch('Invalid/excessive')

    def test_changed_png_is_rejected(self):
        (self.work/'png/equation0001.png').write_bytes(b'changed-image')
        self.reject_patch('preview hash')


if __name__ == '__main__':
    main()

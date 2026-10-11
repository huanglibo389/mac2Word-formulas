#!/usr/bin/env python3
"""Conservative cache repair for source-matched legacy equation objects.

Never creates MathType native data. Unsupported native formats stop prepare.
Audit is diagnostic; successful parsing is not target-editor certification.
"""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree
from PIL import Image
from io import BytesIO
import argparse
import copy
import hashlib
import json
import math
import posixpath
import re
import struct

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
V = 'urn:schemas-microsoft-com:vml'
O = 'urn:schemas-microsoft-com:office:office'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS = dict(w=W, m=M, v=V, o=O)
XML = etree.XMLParser(resolve_entities=False, no_network=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def new_report_path(path, inputs=()):
    output = Path(path).resolve()
    if output in {Path(p).resolve() for p in inputs} or output.exists():
        raise ValueError('Report/output already exists or aliases an input; use a new path')
    return output


def validate_geometry(formula):
    for key in ('width_pt', 'height_pt'):
        value = formula.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 600):
            raise ValueError('Invalid/excessive formula box')
    change = formula.get('geometry_change', {})
    if not isinstance(change, dict) or set(change) - {'width_pt', 'height_pt'}:
        raise ValueError('Geometry may only change width_pt/height_pt')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v != formula[k] for k, v in change.items()):
        raise ValueError('Geometry override disagrees with the rendered box')


def load(path):
    with ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP members; stop rather than guess')
        return {n: z.read(n) for n in names}


def parse(data):
    return etree.fromstring(data, parser=XML)


def rel_part(part):
    p = PurePosixPath(part)
    return str(p.parent / '_rels' / (p.name + '.rels'))


def resolve_target(part, rel):
    if rel.get('TargetMode') == 'External':
        raise ValueError('External target is not a local equation cache')
    target = rel.get('Target', '')
    out = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part), target))
    if out.startswith('../') or out == '..':
        raise ValueError('Target escapes package')
    return out


def equation_objects(root):
    result = []
    for obj in root.xpath('//w:object', namespaces=NS):
        ole = obj.find(f'.//{{{O}}}OLEObject')
        if ole is not None and ole.get('ProgID', '') in ('Equation.3', 'Equation.DSMT4'):
            result.append(obj)
    return result


class CFB:
    """Bounded reader for flat Equation Native streams, not a general OLE editor."""
    def __init__(self, data):
        self.data = data
        if len(data) < 512 or data[:8] != bytes.fromhex('d0cf11e0a1b11ae1'):
            raise ValueError('Not a CFB object')
        self.ss = 1 << struct.unpack_from('<H', data, 30)[0]
        self.ms = 1 << struct.unpack_from('<H', data, 32)[0]
        if self.ss not in (512, 4096) or self.ms != 64 or len(data) % self.ss:
            raise ValueError('Unsupported CFB geometry')
        self.count = len(data) // self.ss - 1
        fat_count = struct.unpack_from('<I', data, 44)[0]
        difat_count = struct.unpack_from('<I', data, 72)[0]
        if fat_count > self.count or difat_count > self.count:
            raise ValueError('Impossible FAT sizes')
        difat = list(struct.unpack_from('<109I', data, 76))
        at = struct.unpack_from('<I', data, 68)[0]
        seen = set()
        for _ in range(difat_count):
            if at in seen:
                raise ValueError('Cyclic DIFAT')
            seen.add(at)
            values = struct.unpack('<%dI' % (self.ss // 4), self.sec(at))
            difat.extend(values[:-1])
            at = values[-1]
        sectors = [s for s in difat if s < 0xfffffffa]
        if len(sectors) != fat_count or len(set(sectors)) != len(sectors):
            raise ValueError('Ambiguous FAT')
        self.fat = []
        for s in sectors:
            self.fat.extend(struct.unpack('<%dI' % (self.ss // 4), self.sec(s)))
        directory = self.chain(struct.unpack_from('<I', data, 48)[0])
        self.entries = []
        for off in range(0, len(directory), 128):
            e = directory[off:off+128]
            length = struct.unpack_from('<H', e, 64)[0]
            if not length:
                continue
            if length < 2 or length > 64 or length % 2:
                raise ValueError('Bad directory entry')
            self.entries.append(dict(name=e[:length-2].decode('utf-16le'), type=e[66],
                                     start=struct.unpack_from('<I', e, 116)[0],
                                     size=struct.unpack_from('<Q', e, 120)[0]))
        roots = [e for e in self.entries if e['type'] == 5]
        if len(roots) != 1:
            raise ValueError('Ambiguous CFB root')
        root = roots[0]
        self.mini = self.chain(root['start'])[:root['size']]
        mf = self.chain(struct.unpack_from('<I', data, 60)[0])
        self.mfat = list(struct.unpack('<%dI' % (len(mf)//4), mf))
        self.cutoff = struct.unpack_from('<I', data, 56)[0]
        if self.cutoff != 4096:
            raise ValueError('Unsupported mini-stream cutoff')

    def sec(self, n):
        if n >= self.count:
            raise ValueError('Sector outside CFB')
        return self.data[(n+1)*self.ss:(n+2)*self.ss]

    def chain(self, n):
        blocks, seen = [], set()
        while n != 0xfffffffe:
            if n >= self.count or n >= len(self.fat) or n in seen:
                raise ValueError('Invalid or cyclic sector chain')
            seen.add(n)
            blocks.append(self.sec(n))
            n = self.fat[n]
        return b''.join(blocks)

    def stream(self, name):
        entries = [e for e in self.entries if e['type'] == 2 and e['name'] == name]
        if len(entries) != 1:
            raise ValueError('Missing or ambiguous stream: ' + name)
        e = entries[0]
        if not e['size']:
            return b''
        if e['size'] >= self.cutoff:
            data = self.chain(e['start'])
        else:
            blocks, seen, n = [], set(), e['start']
            while n != 0xfffffffe:
                if n >= len(self.mfat) or (n+1)*self.ms > len(self.mini) or n in seen:
                    raise ValueError('Invalid mini-sector chain')
                seen.add(n)
                blocks.append(self.mini[n*self.ms:(n+1)*self.ms])
                n = self.mfat[n]
            data = b''.join(blocks)
        if len(data) < e['size']:
            raise ValueError('Truncated stream')
        return data[:e['size']]


def native_v3_details(blob):
    """Read supported v3 records, retaining slots rather than flattening storage."""
    native = CFB(blob).stream('Equation Native')
    if len(native) < 12:
        raise ValueError('Truncated native header')
    header = struct.unpack_from('<I', native)[0]
    size = struct.unpack_from('<I', native, 8)[0]
    if header < 12 or header + size != len(native):
        raise ValueError('Unsupported native header/length')
    data = native[header:]
    if len(data) < 5 or data[0] != 3:
        raise ValueError('Automatic matching supports MTEF 3 only')
    position, chars, records = 5, [], 0

    def rd(n=1):
        nonlocal position
        value = data[position:position+n]
        if len(value) != n:
            raise ValueError('Truncated MTEF record')
        position += n
        return value[0] if n == 1 else value

    def cstring():
        out = bytearray()
        while True:
            v = rd()
            if not v:
                return bytes(out)
            out.append(v)

    def walk(depth=0):
        nonlocal records
        if depth > 100:
            raise ValueError('Excessive MTEF nesting')
        nodes = []
        while True:
            t = rd()
            kind = t & 15
            node = dict(kind=kind)
            records += 1
            if records > 100000:
                raise ValueError('Excessive MTEF records')
            if t & 128:
                x, y = rd(), rd()
                if x == y == 128:
                    rd(4)
            if kind == 0:
                return nodes
            if kind == 1:
                node['null'] = bool(t & 16)
                if not t & 16:
                    node['children'] = walk(depth+1)
            elif kind == 2:
                rd()  # Typeface; parsing text alone does not validate its style.
                node['char'] = chr(struct.unpack('<H', rd(2))[0])
                chars.append(node['char'])
                if t & 32:
                    node['embellishments'] = walk(depth+1)
            elif kind == 3:
                node['selector'], node['variation'], node['options'] = rd(), rd(), rd()
                node['children'] = walk(depth+1)
            elif kind == 4:
                rd(2)
                node['children'] = walk(depth+1)
            elif kind == 5:
                rd(3)
                rows, cols = rd(), rd()
                rd(((rows+1)*2+7)//8 + ((cols+1)*2+7)//8)
                node['children'] = walk(depth+1)
            elif kind == 6:
                cstring()
            elif kind == 7:
                rd(3*rd())
            elif kind == 8:
                rd(2)
                cstring()
            elif kind == 9:
                size_code = rd()
                rd(2 if size_code == 101 else 3 if size_code == 100 else 1)
            elif not 10 <= kind <= 14:
                raise ValueError('Unsupported MTEF record')
            nodes.append(node)
    tree = walk()
    if any(data[position:]):
        raise ValueError('Unparsed MTEF bytes')
    return dict(text=native_logical_text(tree), storage_text=''.join(chars),
                summations=[native_sum_slots(n) for n in iter_native_nodes(tree)
                            if n['kind'] == 3 and n['selector'] == 29])


def iter_native_nodes(nodes):
    for node in nodes:
        yield node
        yield from iter_native_nodes(node.get('children', []))


def native_sum_slots(node):
    """Only the observed SUM template (29, variations 0/1) is supported here."""
    children = node.get('children', [])
    lines = [n for n in children if n['kind'] == 1]
    symbols = [n for n in children if n['kind'] == 2]
    if (node.get('variation') not in (0, 1) or node.get('options') != 0 or len(lines) not in (2, 3)
            or len(symbols) != 1 or symbols[0]['char'] != '∑'
            or any(n['kind'] in (3, 4, 5) for n in children)):
        raise ValueError('Unsupported MTEF summation template/slots')
    upper = native_logical_text(lines[2].get('children', [])) if len(lines) == 3 else ''
    upper_hidden = node['variation'] == 0
    if (upper_hidden and norm(upper)) or (not upper_hidden and len(lines) != 3):
        raise ValueError('Unsupported MTEF summation upper-limit representation')
    return dict(symbol='∑', lower=native_logical_text(lines[1].get('children', [])),
                upper=upper, body=native_logical_text(lines[0].get('children', [])),
                upper_hidden=upper_hidden)


def native_logical_text(nodes):
    out = []
    for node in nodes:
        if node['kind'] == 2:
            out.append(node['char'])
            out.append(native_logical_text(node.get('embellishments', [])))
        elif node['kind'] == 3 and node['selector'] == 29:
            slots = native_sum_slots(node)
            out.extend(slots[k] for k in ('symbol', 'lower', 'upper', 'body'))
        else:
            out.append(native_logical_text(node.get('children', [])))
    return ''.join(out)


def native_v3_text(blob):
    return native_v3_details(blob)['text']


def norm(s):
    # Preserve all visible symbols. Do not normalize variable Z into integer set ℤ.
    return ''.join(s.split()).translate({ord(c): None for c in '\u200b\u2061\u2062\u2063'}).replace('∣', '|')


def source_text(eq):
    if eq is None:
        return ''
    local = etree.QName(eq).localname
    if local.endswith('Pr'):
        return ''
    if local == 't':
        text = eq.text or ''
        run = eq.getparent()
        if run.xpath('./m:rPr/m:scr[@m:val="double-struck"]', namespaces=NS):
            text = text.translate(str.maketrans(dict(zip('CHNPQRZ', 'ℂℍℕℙℚℝℤ'))))
        return text
    if local == 'nary':
        char = eq.find(f'{{{M}}}naryPr/{{{M}}}chr')
        if char is None or not char.get(f'{{{M}}}val'):
            raise ValueError('Source n-ary operator must be explicit for automatic pairing')
        symbol = char.get(f'{{{M}}}val')
        return symbol + ''.join(source_text(eq.find(f'{{{M}}}{k}')) for k in ('sub', 'sup', 'e'))
    return ''.join(source_text(child) for child in eq)


def source_sum_slots(eq):
    result = []
    for node in eq.xpath('.//m:nary', namespaces=NS):
        props = node.find(f'{{{M}}}naryPr')
        char = props.find(f'{{{M}}}chr') if props is not None else None
        symbol = char.get(f'{{{M}}}val') if char is not None else None
        def hidden(tag):
            flag = props.find(f'{{{M}}}{tag}') if props is not None else None
            value = flag.get(f'{{{M}}}val', 'on') if flag is not None else 'off'
            if value not in ('on', '1', 'true', 'off', '0', 'false'):
                raise ValueError('Unsupported source summation visibility value')
            return value in ('on', '1', 'true')
        upper = source_text(node.find(f'{{{M}}}sup'))
        if symbol != '∑' or hidden('subHide') or (hidden('supHide') and norm(upper)):
            raise ValueError('Unsupported source n-ary operator or hidden-limit representation')
        result.append(dict(symbol=symbol, lower=source_text(node.find(f'{{{M}}}sub')),
                           upper=upper, body=source_text(node.find(f'{{{M}}}e')),
                           upper_hidden=hidden('supHide')))
    return result


def verify_sum_slots(source, native):
    expected = source_sum_slots(source)
    if len(expected) != len(native):
        raise ValueError('Summation count mismatch')
    for index, (a, b) in enumerate(zip(expected, native), 1):
        if (a['upper_hidden'] != b['upper_hidden']
                or any(norm(a[k]) != norm(b[k]) for k in ('symbol', 'lower', 'upper', 'body'))):
            raise ValueError(f'Summation {index}: operator, limits, body or visibility mismatch')
    return len(expected)


def emf_diagnostics(data):
    fonts, strings, glyph_index = [], [], False
    at = 0
    while at < len(data):
        if at + 8 > len(data):
            raise ValueError('Truncated EMF')
        kind, size = struct.unpack_from('<II', data, at)
        if size < 8 or at + size > len(data):
            raise ValueError('Bad EMF record length')
        rec = data[at:at+size]
        if kind == 82 and size >= 104:  # EMR_EXTCREATEFONTINDIRECTW
            fonts.append(rec[40:104].decode('utf-16le').split('\0')[0])
        if kind == 84 and size >= 76:  # EMR_EXTTEXTOUTW
            count, offset, flags = struct.unpack_from('<III', rec, 44)
            glyph_index |= bool(flags & 16)
            if not flags & 16 and offset + count*2 <= size:
                strings.append(rec[offset:offset+count*2].decode('utf-16le'))
        at += size
    return dict(fonts=sorted(set(fonts)), text=''.join(strings), glyph_index=glyph_index)


def object_info(files, part, obj, index):
    rels = parse(files[rel_part(part)])
    byid = {r.get('Id'): r for r in rels}
    ole = obj.find(f'.//{{{O}}}OLEObject')
    image = obj.find(f'.//{{{V}}}imagedata')
    if image is None:
        raise ValueError('Unsupported preview representation')
    native_path = resolve_target(part, byid[ole.get(f'{{{R}}}id')])
    preview_path = resolve_target(part, byid[image.get(f'{{{R}}}id')])
    native = files[native_path]
    info = dict(index=index, progid=ole.get('ProgID'), native_part=native_path,
                native_sha256=sha(native), preview_part=preview_path,
                preview_rid=image.get(f'{{{R}}}id'))
    try:
        details = native_v3_details(native)
        info['native_text'] = details['text']
        info['native_storage_text'] = details['storage_text']
        info['native_summations'] = details['summations']
        info['native_parse'] = 'supported-mtef3'
    except (ValueError, IndexError, struct.error, UnicodeError) as exc:
        info['native_parse'] = 'unsupported-or-invalid'
        info['native_error'] = str(exc)
    if preview_path.lower().endswith('.emf'):
        info['emf'] = emf_diagnostics(files[preview_path])
    return info


def audit(args):
    output = new_report_path(args.report, (args.docx,))
    files = load(args.docx)
    parts = []
    for part in sorted(files):
        if not part.startswith('word/') or not part.endswith('.xml'):
            continue
        root = parse(files[part])
        eqs = equation_objects(root)
        omml = root.xpath('//m:oMath', namespaces=NS)
        all_oles = root.findall(f'.//{{{O}}}OLEObject')
        if eqs or omml or all_oles:
            parts.append(dict(part=part, omml_count=len(omml),
                              all_ole_progids=[e.get('ProgID', '') for e in all_oles],
                              objects=[object_info(files, part, e, i) for i, e in enumerate(eqs, 1)]))
    result = dict(docx=str(Path(args.docx).resolve()), docx_sha256=sha(Path(args.docx).read_bytes()),
                  parts=parts, target_editor_verified=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    print('Audit written;', sum(len(p['objects']) for p in parts), 'legacy objects. Target editor NOT tested.')


def prepare(args):
    work = Path(args.work_dir)
    if (any((work/name).exists() for name in ('formulas.json', 'formulas.before-natural.json', 'render_completion.json'))
            or any((work/'png').glob('equation*.png'))):
        raise ValueError('Prepared/rendered work directory already exists; use a fresh directory')
    files, sources = load(args.docx), load(args.source_omml)
    root, source = parse(files[args.part]), parse(sources[args.source_part or args.part])
    objects, eqs = equation_objects(root), source.xpath('//m:oMath', namespaces=NS)
    if not objects or len(objects) != len(eqs):
        raise ValueError('Object/source count mismatch; do not guess a mapping')
    transform = etree.XSLT(etree.parse(args.xslt, parser=XML), access_control=etree.XSLTAccessControl.DENY_ALL)
    tags = {int(x) for x in args.tag_indices.split(',') if x}
    geometry = json.loads(Path(args.geometry).read_text()) if args.geometry else {}
    if any(int(i) < 1 or int(i) > len(objects) for i in geometry) or any(i < 1 or i > len(objects) for i in tags):
        raise ValueError('Override/tag index outside object range')
    formulas = []
    for index, (obj, eq) in enumerate(zip(objects, eqs), 1):
        info = object_info(files, args.part, obj, index)
        text = source_text(eq)
        if info.get('native_parse') != 'supported-mtef3' or norm(info['native_text']) != norm(text):
            raise ValueError(f'Formula {index}: native/source mismatch or unsupported native format')
        try:
            info['summation_slots_verified'] = verify_sum_slots(eq, info['native_summations'])
        except ValueError as exc:
            raise ValueError(f'Formula {index}: {exc}') from exc
        shape = obj.find(f'.//{{{V}}}shape')
        style = shape.get('style', '')
        width = re.search(r'(?:^|;)\s*width:([\d.]+)pt', style)
        height = re.search(r'(?:^|;)\s*height:([\d.]+)pt', style)
        if not width or not height:
            raise ValueError('Unsupported shape units')
        math = parse(str(transform(copy.deepcopy(eq))).encode())
        math.attrib.pop('style', None)
        if norm(''.join(math.itertext())) != norm(text):
            raise ValueError(f'Formula {index}: OMML→MathML character mismatch; inspect styles/accents')
        size = dict(width_pt=float(width.group(1)), height_pt=float(height.group(1)))
        change = geometry.get(str(index), {})
        if set(change) - set(size):
            raise ValueError('Geometry may only change width_pt/height_pt')
        size.update(change)
        validate_geometry(dict(**size, geometry_change=change))
        formulas.append(dict(**info, source_text=text, mathml=etree.tostring(math, encoding='unicode'),
                             confirmed_equation_tag=index in tags, geometry_change=change, **size))
    work.mkdir(parents=True, exist_ok=True)
    manifest = dict(docx=str(Path(args.docx).resolve()), docx_sha256=sha(Path(args.docx).read_bytes()),
                    source_omml=str(Path(args.source_omml).resolve()), source_sha256=sha(Path(args.source_omml).read_bytes()),
                    part=args.part, formulas=formulas)
    with (work/'formulas.json').open('x', encoding='utf8') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('Prepared', len(formulas), 'source-matched formulas. Structure/visual QA still required.')


def patch(args):
    work = Path(args.work_dir)
    manifest_bytes = (work/'formulas.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    source_path = Path(args.docx or manifest['docx']).resolve()
    out = Path(args.out).resolve()
    if out == source_path or out.exists():
        raise ValueError('Use a new output path; never overwrite source or existing file')
    validation_path = new_report_path(work/'patch_validation.json',
        (source_path, manifest['source_omml'], out, work/'formulas.json'))
    if sha(source_path.read_bytes()) != manifest['docx_sha256']:
        raise ValueError('Source changed after preparation')
    if sha(Path(manifest['source_omml']).read_bytes()) != manifest['source_sha256']:
        raise ValueError('Paired OMML source changed after preparation')
    original = load(source_path)
    files = dict(original)
    part = manifest['part']
    root = parse(files[part])
    source_root = copy.deepcopy(root)
    rels = parse(files[rel_part(part)])
    ids = {r.get('Id') for r in rels}
    objects = equation_objects(root)
    if len(objects) != len(manifest['formulas']):
        raise ValueError('Object count changed')
    if (any(type(f.get('index')) is not int for f in manifest['formulas'])
            or [f.get('index') for f in manifest['formulas']] != list(range(1, len(objects)+1))):
        raise ValueError('Formula indices must be unique, consecutive and match object order')
    for formula in manifest['formulas']:
        validate_geometry(formula)
    completion_path = work/'render_completion.json'
    if not completion_path.exists():
        raise ValueError('Missing current render completion record; render all previews first')
    completion = json.loads(completion_path.read_text())
    if (completion.get('schema') != 1 or completion.get('manifest_sha256') != sha(manifest_bytes)
            or Path(completion.get('png_directory', '')).resolve() != (work/'png').resolve()
            or [r.get('index') for r in completion.get('previews', [])] != list(range(1, len(objects)+1))):
        raise ValueError('Render completion does not bind the current full manifest and PNG directory')
    records = completion['previews']
    # Never delete old images or retarget a shared relationship.
    for obj, f, record in zip(objects, manifest['formulas'], records):
        i = f['index']
        png = (work/'png'/f'equation{i:04}.png').read_bytes()
        if (record.get('png_sha256') != sha(png)
                or record.get('mathml_sha256') != sha(f['mathml'].encode('utf8'))
                or record.get('native_sha256') != f['native_sha256']
                or any(record.get(k) != f[k] for k in ('width_pt', 'height_pt'))):
            raise ValueError(f'Formula {i}: preview hash or source/geometry binding mismatch')
        actual = object_info(original, part, obj, i)
        if any(actual[k] != f[k] for k in ('index', 'progid', 'native_part', 'native_sha256', 'preview_part', 'preview_rid')):
            raise ValueError(f'Formula {i}: original object/preview mapping mismatch')
        if not png.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Invalid preview PNG')
        with Image.open(BytesIO(png)) as preview:
            if preview.format != 'PNG' or min(preview.size) <= 0:
                raise ValueError('Invalid preview image format/size')
            preview.verify()  # Validate chunks/CRC before writing any output.
        with Image.open(BytesIO(png)) as preview:
            preview.load()  # Reject valid headers with truncated image data.
        if sha(files[f['native_part']]) != f['native_sha256']:
            raise ValueError('Native payload no longer matches')
        token = 1
        rid = f'rIdPreviewRepair{token}'
        while rid in ids:
            token += 1
            rid = f'rIdPreviewRepair{token}'
        ids.add(rid)
        media = f'word/media/preview_repair_{sha(png)[:12]}_{i:04}.png'
        if media in files and files[media] != png:
            raise ValueError('Media name collision')
        files[media] = png
        etree.SubElement(rels, f'{{{REL}}}Relationship', Id=rid,
                         Type=R+'/image', Target=posixpath.relpath(media, posixpath.dirname(part)))
        image = obj.find(f'.//{{{V}}}imagedata')
        if image.get(f'{{{R}}}id') != f['preview_rid']:
            raise ValueError('Preview mapping changed')
        image.set(f'{{{R}}}id', rid)
        shape = obj.find(f'.//{{{V}}}shape')
        for axis in ('width', 'height'):
            if axis+'_pt' in f['geometry_change']:
                shape.set('style', re.sub(r'\b'+axis+r':[\d.]+pt',
                          f'{axis}:{f[axis+"_pt"]:g}pt', shape.get('style')))
        # Remaining image/OLE relationships are left exactly as they were.
    # Independent difference check: undo only authorized changes in the candidate
    # and compare against the untouched original tree.
    restored = copy.deepcopy(root)
    for old_obj, new_obj, f in zip(equation_objects(source_root), equation_objects(restored), manifest['formulas']):
        new_obj.find(f'.//{{{V}}}imagedata').set(f'{{{R}}}id', f['preview_rid'])
        if f['geometry_change']:
            old_style = old_obj.find(f'.//{{{V}}}shape').get('style')
            new_shape = new_obj.find(f'.//{{{V}}}shape')
            restored_style = new_shape.get('style')
            for axis in ('width', 'height'):
                if axis+'_pt' in f['geometry_change']:
                    old_value = re.search(r'\b'+axis+r':([\d.]+)pt', old_style).group(1)
                    restored_style = re.sub(r'\b'+axis+r':[\d.]+pt', f'{axis}:{old_value}pt', restored_style)
            new_shape.set('style', restored_style)
    if etree.tostring(source_root, method='c14n') != etree.tostring(restored, method='c14n'):
        raise ValueError('Unexpected document changes')
    cts = parse(files['[Content_Types].xml'])
    if not cts.xpath('./ct:Default[@Extension="png"]', namespaces=dict(ct=CT)):
        etree.SubElement(cts, f'{{{CT}}}Default', Extension='png', ContentType='image/png')
    for name, tree in ((part, root), (rel_part(part), rels), ('[Content_Types].xml', cts)):
        files[name] = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
    for name in original:
        if name not in (part, rel_part(part), '[Content_Types].xml') and files[name] != original[name]:
            raise ValueError('Unexpected package change: '+name)
        if name.startswith('word/embeddings/') and files[name] != original[name]:
            raise ValueError('Native data changed: '+name)
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, 'x', ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    info = dict(output=str(out), output_sha256=sha(out.read_bytes()), formulas=len(objects),
                all_original_embeddings_byte_identical=True, source_sha256=manifest['docx_sha256'],
                geometry_changes={str(f['index']):f['geometry_change'] for f in manifest['formulas'] if f['geometry_change']},
                target_editor_verified=False)
    with validation_path.open('x', encoding='utf8') as stream:
        stream.write(json.dumps(info, ensure_ascii=False, indent=2))
    print(json.dumps(info, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('audit')
    a.add_argument('--docx', required=True)
    a.add_argument('--report', required=True)
    a.set_defaults(func=audit)
    a = sub.add_parser('prepare')
    a.add_argument('--docx', required=True)
    a.add_argument('--source-omml', required=True)
    a.add_argument('--xslt', required=True)
    a.add_argument('--work-dir', required=True)
    a.add_argument('--part', default='word/document.xml')
    a.add_argument('--source-part')
    a.add_argument('--tag-indices', default='')
    a.add_argument('--geometry')
    a.set_defaults(func=prepare)
    a = sub.add_parser('patch')
    a.add_argument('--work-dir', required=True)
    a.add_argument('--out', required=True)
    a.add_argument('--docx')
    a.set_defaults(func=patch)
    args = p.parse_args()
    try:
        args.func(args)
    except (ValueError, KeyError, OSError, etree.Error, struct.error) as exc:
        p.exit(2, f'STOP: {exc}\n')


if __name__ == '__main__':
    main()

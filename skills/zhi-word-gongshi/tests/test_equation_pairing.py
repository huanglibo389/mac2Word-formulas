"""Regression checks for reading order and distinct summation slots; no Office needed."""
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
import importlib.util
import struct

spec = importlib.util.spec_from_file_location(
    'legacy_equations', Path(__file__).resolve().parents[1]/'scripts'/'legacy_equations.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def chars(text):
    return b''.join(b'\x02\x83'+struct.pack('<H', ord(c)) for c in text)


def line(text):
    return b'\x01'+chars(text)+b'\x00'


def source(body='ab', lower='j=1', upper='n', hidden=False, symbol='∑'):
    flag = '<m:supHide m:val="on"/>' if hidden else ''
    return legacy.parse((f'<m:oMath xmlns:m="{legacy.M}"><m:nary><m:naryPr>'
        f'<m:chr m:val="{symbol}"/>{flag}</m:naryPr>'
        f'<m:sub><m:r><m:t>{lower}</m:t></m:r></m:sub>'
        f'<m:sup><m:r><m:t>{upper}</m:t></m:r></m:sup>'
        f'<m:e><m:r><m:t>{body}</m:t></m:r></m:e></m:nary></m:oMath>').encode())


def native(body='ab', lower='j=1', upper='n', variation=1, header_size=20, options=0):
    # SUM stores body, lower, upper, then its symbol; not OMML reading order.
    slots = line(body)+line(lower)+(b'\x11' if upper == '' else line(upper))+chars('∑')
    records = b'\x03\x1d'+bytes([variation, options])+slots+b'\x00'
    mtef = b'\x03\x01\x01\x03\x00'+b'\x01'+records+b'\x00\x00'
    header = struct.pack('<III', header_size, 0, len(mtef))+bytes(header_size-12)
    class Stream:
        def stream(self, name):
            if name != 'Equation Native':
                raise ValueError(name)
            return header+mtef
    with patch.object(legacy, 'CFB', return_value=Stream()):
        return legacy.native_v3_details(b'unused-container')


class SummationPairing(TestCase):
    def test_storage_order_is_not_reading_order(self):
        info = native()
        self.assertEqual(info['storage_text'], 'abj=1n∑')
        self.assertEqual(info['text'], '∑j=1nab')
        self.assertEqual(legacy.source_text(source()), info['text'])
        self.assertEqual(legacy.verify_sum_slots(source(), info['summations']), 1)

    def test_slot_boundary_swap_is_rejected_even_when_flat_text_matches(self):
        info = native(body='ab', lower='j', upper='=1n')
        self.assertEqual(info['text'], legacy.source_text(source()))
        with self.assertRaisesRegex(ValueError, 'limits'):
            legacy.verify_sum_slots(source(), info['summations'])

    def test_body_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'body'):
            legacy.verify_sum_slots(source(body='ac'), native()['summations'])

    def test_hidden_upper_limit_is_checked(self):
        info = native(upper='', variation=0)
        self.assertEqual(legacy.verify_sum_slots(source(upper='', hidden=True), info['summations']), 1)
        with self.assertRaisesRegex(ValueError, 'visibility'):
            legacy.verify_sum_slots(source(upper=''), info['summations'])

    def test_unsupported_variation_and_hidden_nonempty_limit_stop(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            native(variation=2)
        with self.assertRaisesRegex(ValueError, 'upper-limit'):
            native(variation=0)
        with self.assertRaisesRegex(ValueError, 'hidden-limit'):
            legacy.verify_sum_slots(source(hidden=True), native()['summations'])

    def test_unhandled_operator_stops(self):
        with self.assertRaisesRegex(ValueError, 'operator'):
            legacy.verify_sum_slots(source(symbol='∏'), native()['summations'])

    def test_implicit_operator_and_unknown_native_options_stop(self):
        eq = source()
        properties = eq.find(f'.//{{{legacy.M}}}naryPr')
        properties.remove(properties.find(f'{{{legacy.M}}}chr'))
        with self.assertRaisesRegex(ValueError, 'explicit'):
            legacy.source_text(eq)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            native(options=1)

    def test_styled_Z_is_not_global_variable_replacement(self):
        eq = legacy.parse((f'<m:oMath xmlns:m="{legacy.M}"><m:r><m:t>Z</m:t></m:r>'
            '<m:r><m:rPr><m:scr m:val="double-struck"/></m:rPr><m:t>Z</m:t></m:r>'
            '</m:oMath>').encode())
        self.assertEqual(legacy.source_text(eq), 'Zℤ')

    def test_native_header_length_is_read_not_fixed(self):
        self.assertEqual(native(header_size=12)['text'], native(header_size=32)['text'])


if __name__ == '__main__':
    main()

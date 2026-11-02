# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend


class DisplayOptionsTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown

    image = test_backend.BackendTests.image

    def test_copy_css_uses_fresh_styled_palette_and_fixed_format_without_export(self):
        self.image()
        row = self.b.catalog()['rows'][0]
        self.assertIn('copy-css', self.b.catalog()['actions'])
        shim = self.home/'bin/wl-copy'
        shim.write_text('#!/usr/bin/python3\nimport os,sys,json\nfrom pathlib import Path\nh=Path(os.environ["HOME"])\n(h/"clipboard").write_bytes(sys.stdin.buffer.read())\n(h/"clipboard-argv").write_text(json.dumps(sys.argv[1:]))\n')
        shim.chmod(0o755)
        for fmt in ('json', 'css', 'gpl'):
            selected = row['id'].rsplit(':', 1)[0]+':'+fmt
            result = self.b.action(selected, 'copy-css', True)
            self.assertTrue(result['ok'], result)
            self.assertEqual((self.home/'clipboard').read_bytes(), b':root {\n  --palette-1: #FF0000;\n}\n')
            self.assertEqual(json.loads((self.home/'clipboard-argv').read_text()), ['--type', 'text/plain;charset=utf-8'])
            self.assertEqual(result['preferences']['recent'][0], selected)
        self.assertFalse(self.b.export_root().exists())
        self.assertFalse(self.b.journal())
        self.b.preferences(True, {'options': {'style': 'pastel', 'colorCount': 8}})
        styled = self.b.catalog()['rows'][0]
        result = subprocess.run(['/usr/bin/python3', str(test_backend.BACKEND), 'action', '--id', styled['id'], '--verb', 'copy-css', '--apply'], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout)
        expected = ':root {\n'+''.join(f'  --palette-{i+1}: {color};\n' for i, color in enumerate(styled['swatches']))+'}\n'
        self.assertEqual((self.home/'clipboard').read_text(), expected)
        before = (self.home/'clipboard').read_bytes()
        for payload in ({}, {'prefix': 'evil'}, {'template': 'evil'}, {'path': '/out'}, {'exec': 'touch nope'}, {'colors': ['#FFFFFF']}):
            self.assertFalse(self.b.action(styled['id'], 'copy-css', True, payload)['ok'])
        self.assertFalse(self.b.action(styled['id'], 'copy-css')['ok'])
        self.assertFalse(self.b.action(row['id'], 'copy-css', True)['ok'])
        self.assertEqual((self.home/'clipboard').read_bytes(), before)
        self.assertFalse(self.b.export_root().exists())

    def test_copy_css_rechecks_source_before_clipboard_and_does_not_trust_row_colors(self):
        source = self.image()
        row = self.b.catalog()['rows'][0]
        real_extract = self.b.extract
        def edited(path, **kwargs):
            colors = real_extract(path, **kwargs)
            source.write_bytes(test_backend.png((0, 255, 0)))
            return colors
        with test_backend.patch.object(self.b, 'extract', edited), test_backend.patch.object(self.b, 'run', wraps=self.b.run) as provider:
            self.assertFalse(self.b.action(row['id'], 'copy-css', True)['ok'])
        self.assertFalse(any(call.args[0][0] == 'wl-copy' for call in provider.call_args_list))
        self.assertFalse(self.b.preferences()['preferences']['recent'])
        fresh = self.b.catalog()['rows'][0]['id']
        for bad in ('palette:missing:css', 'palette:'+fresh.split(':')[1]+':sh', 'wall:'+fresh.split(':')[1], 'palette:$(touch nope):css'):
            with test_backend.patch.object(self.b, 'run', side_effect=AssertionError('No provider for invalid IDs')):
                self.assertFalse(self.b.action(bad, 'copy-css', True)['ok'])
        for colors in ([], ['#FFF'], ['#FFFFFF; bad'], ['#000000']*17):
            with test_backend.patch.object(self.b, 'extract', return_value=colors), test_backend.patch.object(self.b, 'run', side_effect=AssertionError('No clipboard for invalid colors')):
                self.assertFalse(self.b.action(fresh, 'copy-css', True)['ok'])
        source.unlink()
        with test_backend.patch.object(self.b, 'extract', side_effect=AssertionError('No stale decode')):
            self.assertFalse(self.b.action(fresh, 'copy-css', True)['ok'])
        self.assertFalse(self.b.export_root().exists())

    def test_copy_css_preserves_export_restore_and_reports_acknowledgement_or_preference_warnings(self):
        self.image(); selected = self.b.catalog()['rows'][0]['id']
        exported = self.b.action(selected, 'export', True)
        self.assertTrue(exported['ok'], exported)
        receipt = (self.b.state_path()/'restore.json').read_bytes()
        export = test_backend.Path(exported['path']); original = export.read_bytes()
        recent = self.b.preferences()['preferences']['recent']
        shim = self.home/'bin/wl-copy'
        for body in ('import sys; sys.exit(3)', 'import sys; sys.stderr.write("warning")'):
            shim.write_text('#!/usr/bin/python3\n'+body+'\n'); shim.chmod(0o755)
            self.assertFalse(self.b.action(selected, 'copy-css', True)['ok'])
            self.assertEqual(self.b.preferences()['preferences']['recent'], recent)
        shim.write_text('#!/usr/bin/python3\nimport sys\nsys.stdin.buffer.read()\n')
        with test_backend.patch.object(self.b.prefs_module(), 'remember', side_effect=ValueError('full')):
            result = self.b.action(selected, 'copy-css', True)
        self.assertTrue(result['ok'], result)
        self.assertIn('could not be saved', result['message'])
        self.assertEqual((self.b.state_path()/'restore.json').read_bytes(), receipt)
        self.assertEqual(export.read_bytes(), original)
        self.assertIn('restore', self.b.export_actions())
        self.assertTrue(self.b.action('restore:last', 'restore', True)['ok'])
        self.assertFalse(export.exists())

    def test_legacy_options_gain_defaults_without_read_time_write(self):
        value = self.b.preferences()['preferences']
        for key in ('density', 'layout', 'sortOrder', 'themeSource', 'fileType'):
            value['options'].pop(key, None)
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        store.parent.mkdir(parents=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(value).encode(); store.write_bytes(raw); store.chmod(0o600)
        defaults = self.b.preferences()['preferences']['options']
        self.assertEqual(defaults['density'], 'comfortable')
        self.assertEqual(defaults['layout'], 'grid')
        self.assertEqual(defaults['sortOrder'], 'default')
        if self.b.PLUGIN == 'theme-gallery': self.assertEqual(defaults['themeSource'], 'all')
        if self.b.PLUGIN == 'wallpaper-browser': self.assertEqual(defaults['fileType'], 'all')
        self.assertEqual(store.read_bytes(), raw)

    def test_common_display_schema_roundtrip_rejection_is_atomic_and_reset(self):
        expected = {
            'density': ('comfortable', ['comfortable', 'compact']),
            'layout': ('grid', ['grid', 'list']),
            'sortOrder': ('default', ['default', 'name', 'favorites-first']),
        }
        initial = self.b.preferences()
        for key, (default, choices) in expected.items():
            self.assertIn(key, initial['settingsSchema'])
            rule = initial['settingsSchema'][key]
            self.assertEqual(rule['type'], 'enum')
            self.assertEqual(rule['default'], default)
            self.assertEqual(rule['choices'], choices)
            self.assertEqual(initial['preferences']['options'][key], default)
        options = {'density': 'compact', 'layout': 'list', 'sortOrder': 'favorites-first'}
        value = self.b.preferences(True, {'options': options})
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        before = store.read_bytes()
        for key in options:
            for bad in (None, True, 1, [], {}, 'invalid', '界'*6000):
                with self.assertRaises(ValueError):
                    self.b.preferences(True, {'options': {key: bad}})
                self.assertEqual(store.read_bytes(), before)
        result = subprocess.run(['/usr/bin/python3', str(test_backend.BACKEND), 'preferences'], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout)
        saved = json.loads(result.stdout)['preferences']['options']
        self.assertEqual({key: saved[key] for key in options}, options)
        reset = self.b.preferences(True, {'reset': True})['preferences']['options']
        for key, (default, _) in expected.items():
            self.assertEqual(reset[key], default)

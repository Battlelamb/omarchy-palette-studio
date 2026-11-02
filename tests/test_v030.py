# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend

class FeatureTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown
    theme = test_backend.BackendTests.theme
    image = test_backend.BackendTests.image

    def cli(self,*args):
        return subprocess.run(['/usr/bin/python3',str(test_backend.BACKEND),*args],capture_output=True,text=True,timeout=8,cwd='/')

    def test_five_styles_adjustable_count_and_variant_export_identity(self):
        wall=self.image();baseline=self.b.catalog()['rows'][0]
        seen={}
        for style in ('vibrant','muted','pastel','monochrome','high-contrast'):
            result=self.cli('preferences','--apply','--payload',json.dumps({'options':{'style':style,'colorCount':12}}))
            self.assertEqual(result.returncode,0,result.stdout)
            row=self.b.catalog()['rows'][0]
            self.assertEqual(row['category'],style)
            self.assertTrue(0<len(row['swatches'])<=12)
            seen[style]=row['swatches'][0]
            self.assertNotEqual(row['id'],baseline['id'])
            exported=self.b.action(row['id'],'export',True)
            self.assertTrue(exported['ok'],exported)
            self.assertEqual(json.loads(test_backend.Path(exported['path']).read_text())['colors'],row['swatches'])
            self.assertTrue(self.b.action('restore:last','restore',True)['ok'])
        self.assertEqual(len(set(seen.values())),5)
        mono=seen['monochrome'];self.assertEqual(mono[1:3],mono[3:5]);self.assertEqual(mono[3:5],mono[5:7])
        self.assertFalse(self.b.action(baseline['id'],'export',True)['ok'])
        for count in (True,1,17,2.5,'6'):
            self.assertNotEqual(self.cli('preferences','--apply','--payload',json.dumps({'options':{'colorCount':count}})).returncode,0,count)
        actual=self.b.run;calls=[]
        def capture(argv,**kwargs): calls.append(argv);return actual(argv,**kwargs)
        with test_backend.patch.object(self.b,'run',capture): self.b.extract(wall)
        self.assertEqual(calls[0][calls[0].index('-colors')+1],'12')

    def test_numeric_contrast_uses_linearized_srgb_and_is_not_certification(self):
        self.image()
        row=self.b.catalog()['rows'][0]
        self.assertIn('contrast',row)
        self.assertAlmostEqual(row['contrast']['againstBlack'][0],5.25,places=2)
        self.assertAlmostEqual(row['contrast']['againstWhite'][0],4.0,places=2)
        self.assertIn('Contrast',row['detail'])
        self.assertEqual(self.b.contrast_ratio('#000000','#FFFFFF'),21.0)
        self.assertEqual(self.b.contrast_ratio('#123456','#123456'),1.0)
        self.assertEqual(self.b.contrast_ratio('#777777','#FFFFFF'),self.b.contrast_ratio('#FFFFFF','#777777'))
        for bad in ('#fff','red','#GG0000',42):
            with self.assertRaises(ValueError): self.b.contrast_ratio(bad,'#FFFFFF')
        colors=['#000000','#FFFFFF','#777777']
        data=self.b.contrast_metrics(colors)
        self.assertEqual(data['pairs'][0],{'a':0,'b':1,'ratio':21.0})
        self.assertEqual(len(data['pairs']),3)

    def test_individual_color_copy_requires_valid_index_and_confirmed_clipboard_acceptance(self):
        self.image();selected=self.b.catalog()['rows'][0]['id']
        shim=self.home/'bin/wl-copy'
        shim.write_text('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\nPath(os.environ["HOME"],"clipboard").write_bytes(sys.stdin.buffer.read())\n');shim.chmod(0o755)
        result=self.b.action(selected,'copy-color',True,{'index':0})
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.home/'clipboard').read_bytes(),b'#FF0000')
        self.assertEqual(result['preferences']['recent'],[selected])
        self.assertFalse(self.b.export_root().exists())
        for payload in (None,{}, {'index':True},{'index':-1},{'index':1},{'index':'0'},{'index':0,'text':'injection'}):
            self.assertFalse(self.b.action(selected,'copy-color',True,payload)['ok'],payload)
        self.assertFalse(self.b.action(selected,'copy-color',False,{'index':0})['ok'])
        shim.write_text('#!/usr/bin/python3\nimport sys\nsys.exit(3)\n')
        self.assertFalse(self.b.action(selected,'copy-color',True,{'index':0})['ok'])
        self.assertFalse(self.b.action('palette:missing:json','copy-color',True,{'index':0})['ok'])
        shim.write_text('#!/usr/bin/python3\n')
        with test_backend.patch.object(self.b.prefs_module(),'remember',side_effect=ValueError('disk full')):
            result=self.b.action(selected,'copy-color',True,{'index':0})
        self.assertTrue(result['ok'],result)
        self.assertIn('could not be saved',result['message'])

    def test_export_restore_uses_receipt_even_with_corrupt_preferences(self):
        self.image();selected=self.b.catalog()['rows'][0]['id']
        result=self.b.action(selected,'export',True);self.assertTrue(result['ok'],result)
        prefs=self.home/'preferences-config/omarchy/lego/palette-studio/preferences.json'
        prefs.write_text('{broken')
        restored=self.b.action('restore:last','restore',True)
        self.assertTrue(restored['ok'],restored)
        self.assertEqual(prefs.read_text(),'{broken')

    def test_documented_cli_style_copy_export_restore_roundtrip(self):
        self.image();shim=self.home/'bin/wl-copy';shim.write_text('#!/usr/bin/python3\nimport sys\nsys.stdin.buffer.read()\n');shim.chmod(0o755)
        result=self.cli('preferences','--apply','--payload','{"options":{"style":"pastel","colorCount":8}}')
        self.assertEqual(result.returncode,0,result.stdout)
        row=json.loads(self.cli('catalog').stdout)['rows'][0];selected=row['id']
        result=self.cli('action','--id',selected,'--verb','copy-color','--payload','{"index":0}','--apply')
        self.assertEqual(result.returncode,0,result.stdout)
        result=self.cli('action','--id',selected,'--verb','export','--apply');self.assertEqual(result.returncode,0,result.stdout)
        target=test_backend.Path(json.loads(result.stdout)['path']);self.assertEqual(json.loads(target.read_text())['colors'],row['swatches'])
        result=self.cli('action','--id','restore:last','--verb','restore','--apply');self.assertEqual(result.returncode,0,result.stdout)
        self.assertFalse(target.exists());self.assertTrue(test_backend.Path(json.loads(result.stdout)['path']).exists())

    def test_real_multicolor_extraction_honors_count_and_exports_above_six_colors(self):
        code,data,err=self.b.run(['/usr/bin/magick','-size','64x64','gradient:red-blue','PNG:-'],timeout=3)
        self.assertEqual(code,0,err)
        path=self.image();path.write_bytes(data)
        self.b.preferences(True,{'options':{'colorCount':16}})
        row=self.b.catalog()['rows'][0]
        self.assertGreater(len(row['swatches']),6);self.assertLessEqual(len(row['swatches']),16)
        result=self.b.action(row['id'],'export',True);self.assertTrue(result['ok'],result)
        self.assertEqual(json.loads(test_backend.Path(result['path']).read_text())['colors'],row['swatches'])
        self.b.preferences(True,{'options':{'colorCount':2}})
        row=self.b.catalog()['rows'][0];self.assertLessEqual(len(row['swatches']),2)
        self.assertEqual(len(row['contrast']['pairs']),1)

    def test_preferences_favorites_read_reset_and_strict_cli(self):
        self.image();row=self.b.catalog()['rows'][0]
        result=self.b.action(row['id'],'favorite',True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['preferences']['favorites'],[row['id']])
        self.assertTrue(self.b.catalog()['rows'][0]['favorite'])
        self.assertFalse(self.b.action('palette:missing:json','favorite',True)['ok'])
        result=self.cli('preferences','--apply','--payload','{"options":{"favoritesOnly":true}}')
        self.assertEqual(result.returncode,0,result.stdout)
        for args in [('catalog','--apply'),('catalog','--payload','{}'),('preferences','--payload','{}'),('preferences','--id','x'),('action','--id',row['id'],'--verb','favorite','--apply','--payload','{}')]:
            self.assertNotEqual(self.cli(*args).returncode,0,args)
        for raw in ('[]','{"options":{},"options":{}}','{"options":{"style":NaN}}','{"options":{"style":"bad"}}','{"version":1}','{"reset":true,"options":{}}'):
            self.assertNotEqual(self.cli('preferences','--apply','--payload',raw).returncode,0,raw)
        self.assertFalse(self.b.action(row['id'],'favorite')['ok'])
        result=self.cli('preferences','--apply','--payload','{"reset":true}')
        self.assertEqual(json.loads(result.stdout)['preferences']['favorites'],[])

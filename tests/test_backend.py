# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import struct
import zlib

BACKEND = Path(__file__).resolve().parents[1] / "runtime/0.1.0/backend.py"
def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*2)*2))+chunk(b'IEND',b'')

class BackendTests(unittest.TestCase):
    def test_restore_retains_held_fd_edit_after_quarantine_hash(self):
        import contextlib
        self.image();selected=self.b.catalog()['rows'][0]['id']
        result=self.b.action(selected,'export',True);self.assertTrue(result['ok'],result)
        target=Path(result['path']);receipt=self.b.journal();actual=self.b.opened
        quarantine_opens=[];edited=b'editor write after verified quarantine hash'
        with target.open('r+b',buffering=0) as editor:
            @contextlib.contextmanager
            def race(path,limit=self.b.MAX_IMAGE):
                with actual(path,limit) as value:
                    if Path(path).name.startswith('.restore-'):
                        quarantine_opens.append(Path(path))
                        if len(quarantine_opens)==2:
                            editor.seek(0);editor.write(edited);editor.truncate();os.fsync(editor.fileno())
                    yield value
            with patch.object(self.b,'opened',race):
                restored=self.b.action(selected,'restore',True)
            self.assertEqual(len(quarantine_opens),2,'mutation must follow the quarantine hash check')
            recovery=quarantine_opens[-1]
            self.assertTrue(recovery.exists(),'held-FD edit was irreversibly unlinked')
            self.assertEqual(recovery.read_bytes(),edited)
            self.assertTrue(restored['ok'],restored)
            self.assertEqual(restored['path'],str(recovery))
            self.assertIn('retained',restored['message'].lower())
            self.assertNotIn('removed',restored['message'].lower())
            self.assertEqual((recovery.stat().st_dev,recovery.stat().st_ino),(receipt['device'],receipt['inode']))
            self.assertFalse(target.exists());self.assertEqual(self.b.journal(),{})
            editor.seek(0);editor.write(b'later edit');editor.truncate();os.fsync(editor.fileno())
            self.assertEqual(recovery.read_bytes(),b'later edit')
        self.assertFalse(self.b.action(selected,'restore',True)['ok'])

    def test_failed_receipt_write_never_publishes_export(self):
        self.image();selected=self.b.catalog()['rows'][0]['id']
        with patch.object(self.b.State,'save',side_effect=OSError('disk full')):
            result=self.b.action(selected,'export',True)
        self.assertFalse(result['ok'])
        public=list(self.b.export_root().glob('*.json')) if self.b.export_root().exists() else []
        self.assertEqual(public,[],'export published without recoverable ownership receipt')

    def test_deeply_nested_journal_fails_closed(self):
        with self.b.State() as state:state.put('restore.json',b'['*2000+b']'*2000)
        self.assertEqual(self.b.journal(),{})
    def test_empty_catalog_is_honest(self):
        result=self.b.catalog()
        self.assertEqual(result['rows'],[])
        self.assertIn('no ',result['message'].lower())
    def test_cli_real_action_and_restore_round_trip(self):
        if self.b.PLUGIN=='theme-gallery':
            self.theme('before',True);self.theme('after',True);self.current_theme('before');self.theme_setter()
            selected,verb='theme:after','apply'
        elif self.b.PLUGIN=='wallpaper-browser':
            old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
            selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new));verb='apply'
        else:
            self.image();selected=self.b.catalog()['rows'][0]['id'];verb='export'
        def cli(v,confirm=True):
            args=['/usr/bin/python3',str(BACKEND),'action','--id',selected,'--verb',v]+(['--apply'] if confirm else [])
            r=subprocess.run(args,capture_output=True,text=True,timeout=10,cwd='/')
            self.assertNotIn('Traceback',r.stderr)
            return r,json.loads(r.stdout)
        r,value=cli(verb,False);self.assertNotEqual(r.returncode,0);self.assertFalse(value['ok'])
        r,value=cli(verb);self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
        r,value=cli('restore');self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
    def test_sandbox_has_no_live_ipc_home_or_network(self):
        self.assertFalse(Path('/srv/projects').exists());self.assertFalse(Path('/home/leviathan').exists())
        self.assertFalse(Path('/run/user').exists())
        interfaces=[line.split(':')[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]]
        self.assertEqual(interfaces,['lo'])
        for key in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE','DBUS_SESSION_BUS_ADDRESS'):
            self.assertNotIn(key,os.environ)

    def test_restore_race_preserves_new_external_file(self):
        self.image();selected=self.b.catalog()['rows'][0]['id']
        result=self.b.action(selected,'export',True);self.assertTrue(result['ok']);target=Path(result['path'])
        actual=self.b.read
        def race(path,*args):
            data=actual(path,*args)
            if Path(path)==target:target.write_text('external edit')
            return data
        with patch.object(self.b,'read',race):self.b.action(selected,'restore',True)
        self.assertTrue(target.exists(),'concurrent external replacement was deleted')
        self.assertEqual(target.read_text(),'external edit')
    def test_all_three_export_formats_contain_extracted_colors(self):
        self.image(rgb=(0,0,255));rows=self.b.catalog()['rows']
        for row in rows:
            result=self.b.action(row['id'],'export',True);self.assertTrue(result['ok'],result)
            value=Path(result['path']).read_text()
            if row['format']=='json':self.assertEqual(json.loads(value)['colors'],['#0000FF'])
            if row['format']=='css':self.assertIn('--palette-1: #0000FF;',value)
            if row['format']=='gpl':self.assertIn('  0   0 255 Color 1',value)
    def test_export_refuses_changed_destination_without_overwrite(self):
        self.image();selected=self.b.catalog()['rows'][0]['id'];first=self.b.action(selected,'export',True)
        target=Path(first['path']);target.write_text('mine')
        self.assertFalse(self.b.action(selected,'export',True)['ok']);self.assertEqual(target.read_text(),'mine')
        self.assertFalse(self.b.action(selected,'restore',True)['ok']);self.assertEqual(target.read_text(),'mine')
    def test_export_is_idempotent_without_rewriting(self):
        self.image();selected=self.b.catalog()['rows'][0]['id'];first=self.b.action(selected,'export',True)
        target=Path(first['path']);stamp=target.stat().st_mtime_ns
        self.assertTrue(self.b.action(selected,'export',True)['ok']);self.assertEqual(target.stat().st_mtime_ns,stamp)
        self.assertTrue(self.b.action(selected,'restore',True)['ok'])
    def test_export_destination_symlink_refused(self):
        self.image();selected=self.b.catalog()['rows'][0]['id']
        outside=self.home/'outside';outside.mkdir();root=self.b.export_root();root.parent.mkdir(parents=True);root.symlink_to(outside,target_is_directory=True)
        self.assertFalse(self.b.action(selected,'export',True)['ok']);self.assertEqual(list(outside.iterdir()),[])
    def test_stale_palette_id_refused(self):
        p=self.image();selected=self.b.catalog()['rows'][0]['id'];p.write_bytes(png((0,255,0)))
        self.assertFalse(self.b.action(selected,'export',True)['ok']);self.assertFalse(self.b.export_root().exists())
    def test_full_membership_beyond_preview_budget(self):
        for i in range(40):self.image('%03d.png'%i)
        selected='palette:'+self.b.token(self.home/'Pictures/Wallpapers/039.png')+':json'
        self.assertNotIn(selected,[r['id'] for r in self.b.catalog()['rows']])
        self.assertTrue(self.b.action(selected,'export',True)['ok'])
    def test_user_wallbash_templates_are_not_executed(self):
        self.image();templates=self.home/'.config/hyde/wallbash';templates.mkdir(parents=True)
        (templates/'evil.dcol').write_text('~/.config/kitty/kitty.conf|touch owned\n<wallbash_pry1>')
        self.b.catalog()
        selected=self.b.catalog()['rows'][0]['id'];self.assertTrue(self.b.action(selected,'export',True)['ok'])
        self.assertFalse((self.home/'owned').exists());self.assertFalse((self.home/'.config/kitty').exists())
    def test_extraction_failure_is_not_fake_palette(self):
        self.image()
        with patch.object(self.b,'run',return_value=(2,b'',b'bad decoder')):
            result=self.b.catalog();self.assertEqual(result['rows'],[])
    def test_export_rechecks_racing_source(self):
        p=self.image();selected=self.b.catalog()['rows'][0]['id'];actual=self.b.extract
        def race(path):
            colors=actual(path);p.write_bytes(png((0,255,0)));return colors
        with patch.object(self.b,'extract',race):self.assertFalse(self.b.action(selected,'export',True)['ok'])
        self.assertFalse(self.b.export_root().exists())
    def test_installed_decodes_jpeg_webp_and_bmp(self):
        for coder,suffix in [('JPEG','jpg'),('WEBP','webp'),('BMP','bmp')]:
            code,data,err=self.b.run(['/usr/bin/magick','PNG:-',coder+':-'],payload=png((0,0,255)),timeout=3)
            self.assertEqual(code,0,err)
            path=self.home/'Pictures/Wallpapers'/('blue.'+suffix);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            colors=self.b.extract(path)
            self.assertTrue(colors);self.assertTrue(all(self.b.HEX.fullmatch(c) for c in colors))

    def test_catalog_is_read_only(self):
        self.image();self.theme()
        before={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.b.catalog()
        after={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(after,before)
    def test_catalog_rejects_symlink_ancestor(self):
        outside=self.home/'external';outside.mkdir();(outside/'escape.png').write_bytes(png())
        (self.home/'Pictures').symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.b.image_files(),[])
    def test_fifo_and_oversized_image_are_not_members(self):
        p=self.image();root=p.parent
        os.mkfifo(root/'pipe.png')
        with (root/'large.png').open('wb') as stream: stream.truncate(self.b.MAX_IMAGE+1)
        self.assertEqual(self.b.image_files(),[p])
    def test_ids_change_on_replacement_but_not_refresh(self):
        p=self.image();first=self.b.token(p)
        self.assertEqual(self.b.token(p),first)
        replacement=p.with_name('replace');replacement.write_bytes(png((0,255,0)));replacement.replace(p)
        self.assertNotEqual(self.b.token(p),first)
    def test_racing_file_read_fails_closed(self):
        p=self.image();actual=self.b.opened
        import contextlib
        @contextlib.contextmanager
        def racing(path,limit):
            with actual(path,limit) as (stream,info):
                class Proxy:
                    def read(inner,n):
                        data=stream.read(n);p.write_bytes(png((0,255,0)));return data
                    def fileno(inner):return stream.fileno()
                yield Proxy(),info
        with patch.object(self.b,'opened',racing):
            with self.assertRaises(ValueError):self.b.read(p,self.b.MAX_IMAGE)
    def test_subprocess_output_is_bounded(self):
        with self.assertRaises(self.b.OperationError):
            self.b.run(['/usr/bin/python3','-c','import sys;sys.stdout.write("x"*1000000)'],timeout=1)
    def test_action_lock_is_exclusive(self):
        with self.b.State():
            with self.assertRaises(OSError):
                with self.b.State():pass
    def test_state_symlink_is_rejected(self):
        dest=self.home/'untouched';dest.mkdir()
        state=self.b.state_path();state.parent.mkdir(parents=True,exist_ok=True);state.symlink_to(dest,target_is_directory=True)
        result=self.b.action('unknown','restore',True)
        self.assertFalse(result['ok']);self.assertEqual(list(dest.iterdir()),[])
    def test_literal_invalid_ids_cannot_execute(self):
        for value in ('../../escape','$(touch injected)',';touch injected','--help','bad\nname','x'*200):
            self.assertFalse(self.b.action(value,'apply',True)['ok'])
            self.assertFalse(self.b.action(value,'restore',True)['ok'])
        self.assertFalse((self.home/'injected').exists())
    def test_scan_budget_does_not_read_overfull_directory(self):
        p=self.image()
        for i in range(self.b.MAX_ENTRIES): (p.parent/str(i)).touch()
        self.assertEqual(self.b.image_files(),[])
    def test_png_dimension_bomb_is_rejected(self):
        p=self.image();data=bytearray(p.read_bytes());data[16:24]=struct.pack('!II',100000,100000);p.write_bytes(data)
        self.assertFalse(self.b.valid_image(p))

    def test_timeout_kills_descendant_after_parent_exits(self):
        script=self.home/'fork.py'
        script.write_text("import os,time,signal\nfrom pathlib import Path\npid=os.fork()\nif pid: Path(os.environ['HOME']+'/child.pid').write_text(str(pid));os._exit(0)\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(30)\n")
        with self.assertRaises(self.b.OperationError): self.b.run(['/usr/bin/python3',str(script)],timeout=0.15)
        pid=int((self.home/'child.pid').read_text())
        import time,signal
        time.sleep(0.05)
        status=Path('/proc')/str(pid)/'stat'
        state=status.read_text().split()[2] if status.exists() else 'gone'
        if state not in ('gone','Z'):
            os.kill(pid,signal.SIGKILL)
        self.assertIn(state,('gone','Z'),'provider descendant survived timeout')

    def test_extractor_uses_frozen_bytes_and_no_user_templates(self):
        p=self.image('literal[3];$x.png')
        actual=self.b.run
        seen=[]
        def capture(argv,**kwargs):
            seen.append((argv,kwargs));return actual(argv,**kwargs)
        with patch.object(self.b,'run',capture): colors=self.b.extract(p)
        self.assertEqual(colors,['#FF0000'])
        self.assertEqual(len(seen),1)
        argv,kwargs=seen[0]
        self.assertIn('PNG:-[0]',argv);self.assertNotIn(str(p),argv)
        self.assertEqual(kwargs['payload'],p.read_bytes())
        self.assertNotEqual(kwargs['env'].get('HOME'),str(self.home))
        self.assertIn('-limit',argv)
    def test_missing_extractor_is_honest_empty(self):
        self.image();self.b.MAGICK='/does/not/exist'
        value=self.b.catalog()
        self.assertEqual(value['rows'],[])
        self.assertIn('unavailable',value['message'].lower())

    def test_selective_export_and_restore_preserves_other_files(self):
        self.image()
        rows=self.b.catalog()['rows']
        selected=next(r['id'] for r in rows if r['format']=='css')
        self.assertFalse(self.b.action(selected,'export')['ok'])
        result=self.b.action(selected,'export',True)
        self.assertTrue(result['ok'],result)
        target=Path(result['path']);self.assertEqual(target.suffix,'.css')
        self.assertIn('--palette-1: #FF0000;',target.read_text())
        unrelated=target.parent/'mine.txt';unrelated.write_text('preserve')
        self.assertEqual([x.suffix for x in target.parent.iterdir() if x.suffix in ('.css','.json','.gpl')],['.css'])
        self.assertIn('restore',self.b.catalog()['actions'])
        exported=target.read_bytes();receipt=self.b.journal()
        restored=self.b.action(selected,'restore',True);self.assertTrue(restored['ok'],restored)
        recovery=Path(restored['path'])
        self.assertTrue(recovery.name.startswith('.restore-'));self.assertEqual(recovery.parent,target.parent)
        self.assertEqual(recovery.read_bytes(),exported)
        self.assertEqual((recovery.stat().st_dev,recovery.stat().st_ino),(receipt['device'],receipt['inode']))
        self.assertIn('retained',restored['message'].lower());self.assertEqual(self.b.journal(),{})
        self.assertNotIn('restore',self.b.catalog()['actions'])
        self.assertFalse(target.exists());self.assertEqual(unrelated.read_text(),'preserve')

    def test_cli_catalog_and_explicit_action_gate(self):
        self.image()
        self.theme()
        def cli(*args):
            return subprocess.run(['/usr/bin/python3',str(BACKEND),*args],capture_output=True,text=True,timeout=8,cwd='/')
        r=cli('catalog');self.assertEqual(r.returncode,0)
        value=json.loads(r.stdout);self.assertIn('rows',value);self.assertIn('actions',value)
        self.assertLess(len(r.stdout.encode()),1048576)
        for args in [('action','--id','../../no','--verb','apply'),('action','--id','bad','--verb','exec','--apply')]:
            r=cli(*args);self.assertNotEqual(r.returncode,0)
            value=json.loads(r.stdout);self.assertFalse(value['ok']);self.assertNotIn('Traceback',r.stderr)

    def test_catalog_rejects_unsafe_members(self):
        good=self.image()
        root=good.parent
        (root/'link.png').symlink_to(good)
        (root/'bad.png').write_bytes(b'not an image')
        (root/'bad.svg').write_text('<svg/>')
        (root/'line\nname.png').write_bytes(png())
        outside=self.home/'outside';outside.mkdir();(outside/'escape.png').write_bytes(png())
        (root/'collection').symlink_to(outside,target_is_directory=True)
        rows=self.b.catalog()['rows']
        self.assertEqual({r['preview'] for r in rows},{str(good)})

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'PATH':str(self.home/'bin')+':/usr/bin'},clear=True)
        self.env.start()
        (self.home/'bin').mkdir()
        spec=importlib.util.spec_from_file_location('backend',BACKEND)
        self.b=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.b)
        self.b.STOCK=self.home/'stock'
        self.b.STOCK.mkdir()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def theme(self,name='test-theme',user=False):
        root=self.home/'.config/omarchy/themes' if user else self.b.STOCK
        p=root/name;p.mkdir(parents=True,exist_ok=True)
        (p/'colors.toml').write_text('accent = "#123456"\n')
        return p
    def image(self,name='red.png',rgb=(255,0,0)):
        p=self.home/'Pictures/Wallpapers'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(png(rgb));return p

    def test_catalog_extracts_actual_wallpaper_colors(self):
        p=self.image()
        self.assertTrue(callable(getattr(self.b,'catalog',None)), 'catalog behavior missing')
        rows=self.b.catalog()['rows']
        self.assertEqual(len(rows),3)
        self.assertEqual({r['format'] for r in rows},{'json','css','gpl'})
        self.assertTrue(all(r['color']=='#FF0000' for r in rows))
        self.assertTrue(all(r['preview']==str(p) for r in rows))

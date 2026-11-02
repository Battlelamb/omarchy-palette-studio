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

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / Path(json.loads((ROOT / 'manifest.json').read_text())['entryPoints']['overlay']).parent / 'backend.py'
def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*2)*2))+chunk(b'IEND',b'')

class BackendTests(unittest.TestCase):
    def test_legacy_large_preferences_preserve_confirmed_action_and_usable_reads(self):
        # Seed an old valid store directly: new writes have a smaller aggregate budget.
        self.image(); selected = self.b.catalog()['rows'][0]['id']; verb = 'export'
        prefs = self.b.preferences()['preferences']
        prefs['collections'] = {'g'+str(g): [str(n).zfill(4)+'x'*252 for n in range(512)] for g in range(9)}
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(self.home/'.config')))
        store = base/'omarchy/lego/palette-studio/preferences.json'
        store.parent.mkdir(parents=True, exist_ok=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(prefs, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertGreater(len(raw), 1048576)
        store.write_bytes(raw); store.chmod(0o600)
        result = subprocess.run(['/usr/bin/python3', str(BACKEND), 'action', '--id', selected, '--verb', verb, '--apply'],  capture_output=True, timeout=12)
        value = json.loads(result.stdout)
        self.assertTrue(list(self.b.export_root().glob('*.json')))
        self.assertEqual(result.returncode, 0, value)
        self.assertTrue(value['ok'], value)
        self.assertIn('warning', value['message'].lower())
        self.assertLessEqual(len(result.stdout), 1048576)
        self.assertNotIn('preferences', value)
        for command in ('preferences',):
            result = subprocess.run(['/usr/bin/python3', str(BACKEND), command],  capture_output=True, timeout=12)
            value = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, value)
            self.assertNotIn('preferences', value)
            self.assertIn('warning', value['message'].lower())
            self.assertLessEqual(len(result.stdout), 1048576)
            if command == 'catalog': self.assertIn(selected, [row['id'] for row in value['rows']])
        reset = subprocess.run(['/usr/bin/python3', str(BACKEND), 'preferences', '--apply', '--payload', '{"reset":true}'],  capture_output=True, timeout=8)
        self.assertEqual(reset.returncode, 0, reset.stdout)
        self.assertEqual(json.loads(reset.stdout)['preferences']['collections'], {})


    def test_restore_last_survives_empty_catalog_and_missing_extractor(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json'
        self.assertTrue(self.b.action(selected,'export',True)['ok'])
        source.unlink();self.b.MAGICK='/missing-extractor'
        value=self.b.catalog();self.assertEqual(value['rows'],[]);self.assertEqual(value['actions'],['export','favorite','copy-color','restore'])
        self.assertFalse(self.b.action('palette:other:json','restore',True)['ok'])
        result=self.b.action('restore:last','restore',True);self.assertTrue(result['ok'],result)
        self.assertEqual(result['actions'],['export','favorite','copy-color'])

    def test_disappearing_selected_source_still_refuses_before_extract(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json';actual=self.b.image_files
        def race():
            files=actual();source.unlink();return files
        with patch.object(self.b,'image_files',race),patch.object(self.b,'extract',wraps=self.b.extract) as decode:
            result=self.b.action(selected,'export',True)
        self.assertFalse(result['ok']);self.assertNotIn('actions',result);decode.assert_not_called()
        self.assertFalse(self.b.export_root().exists())

    def test_identical_concurrent_publication_without_previous_receipt_never_claims_external_file(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json';actual=self.b.os.link
        target=self.b.export_root()/(selected.split(':')[1]+'.json');expected=self.b.render(['#FF0000'],'json')
        def race(src,dst,**kwargs):
            target.write_bytes(expected);return actual(src,dst,**kwargs)
        with patch.object(self.b.os,'link',race):failed=self.b.action(selected,'export',True)
        self.assertFalse(failed['ok']);self.assertNotIn('actions',failed)
        self.assertEqual(self.b.journal(),{});self.assertEqual(target.read_bytes(),expected)
        retry=self.b.action(selected,'export',True)
        self.assertTrue(retry['ok'],retry);self.assertEqual(retry.get('actions'),['export','favorite','copy-color'])
        self.assertFalse(self.b.action('restore:last','restore',True)['ok'])
        self.assertEqual(target.read_bytes(),expected)

    def test_publication_boundaries_keep_the_receipt_for_the_published_inode(self):
        self.image('aaa.png');source=self.image('bbb.png',(0,0,255))
        first_id='palette:'+self.b.token(self.home/'Pictures/Wallpapers/aaa.png')+':json'
        selected='palette:'+self.b.token(source)+':json'
        self.assertTrue(self.b.action(first_id,'export',True)['ok']);previous=self.b.journal()
        target=self.b.export_root()/(selected.split(':')[1]+'.json')
        boundaries=('receipt-file-fsync','receipt-replace-before','receipt-replace-after','crash-after-replace','link-error-after-publication','crash-after-link','export-dir-fsync','final-save-before','final-save-after')
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                if target.exists():target.unlink()
                with self.b.State() as state:state.save(previous)
                pid=os.fork()
                if pid==0:
                    actual_fsync=os.fsync;actual_replace=os.replace;actual_link=os.link;actual_save=self.b.State.save
                    saves=0
                    def save(state,value):
                        nonlocal saves
                        saves+=1
                        if saves==2 and boundary=='final-save-before':raise OSError('full before final save')
                        actual_save(state,value)
                        if saves==2 and boundary=='final-save-after':raise OSError('full after final save')
                    def fsync(fd):
                        path=Path(os.readlink('/proc/self/fd/'+str(fd)))
                        if boundary=='receipt-file-fsync' and path.name.startswith('.write-'):raise OSError('file fsync failed')
                        if boundary=='export-dir-fsync' and path==self.b.export_root():raise OSError('export fsync failed')
                        return actual_fsync(fd)
                    def replace(src,dst,**kwargs):
                        if dst=='restore.json' and boundary=='receipt-replace-before':raise OSError('replace failed')
                        actual_replace(src,dst,**kwargs)
                        if dst=='restore.json' and boundary=='receipt-replace-after':raise OSError('replace persisted but fsync failed')
                        if dst=='restore.json' and boundary=='crash-after-replace':os._exit(73)
                    def link(src,dst,**kwargs):
                        actual_link(src,dst,**kwargs)
                        if boundary=='link-error-after-publication':raise OSError('interrupted after publication')
                        if boundary=='crash-after-link':os._exit(73)
                    with patch.object(self.b.State,'save',save),patch.object(os,'fsync',fsync),patch.object(os,'replace',replace),patch.object(os,'link',link):
                        result=self.b.action(selected,'export',True)
                    os._exit(0 if not result['ok'] and 'actions' not in result else 74)
                _,status=os.waitpid(pid,0)
                self.assertEqual(os.waitstatus_to_exitcode(status),73 if boundary.startswith('crash-') else 0)
                published=boundary in ('link-error-after-publication','crash-after-link','export-dir-fsync','final-save-before','final-save-after')
                receipt=self.b.journal()
                self.assertEqual(receipt['selected'],selected if published else first_id)
                if published:
                    self.assertEqual(target.read_bytes(),self.b.render(['#0000FF'],'json'))
                    self.assertEqual(target.stat().st_ino,receipt['inode'])
                    restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
                else:self.assertFalse(target.exists())
        with self.b.State() as state:state.save(previous)
        self.assertTrue(self.b.action(first_id,'restore',True)['ok'])

    def test_action_hints_replace_only_capabilities_without_catalog_decode(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json'
        for repeat in range(2):
            with patch.object(self.b,'extract',wraps=self.b.extract) as decode,patch.object(self.b,'catalog',side_effect=AssertionError('catalog reload')):
                result=self.b.action(selected,'export',True)
            self.assertTrue(result['ok'],result);self.assertEqual(result.get('actions'),['export','favorite','copy-color','restore'])
            self.assertEqual(decode.call_count,1)
        restored=self.b.action('restore:last','restore',True)
        self.assertTrue(restored['ok'],restored);self.assertEqual(restored.get('actions'),['export','favorite','copy-color'])
        # Matching external bytes are not ownership, nor permission to detach them.
        target=Path(result['path']);target.write_bytes(self.b.render(['#FF0000'],'json'));stamp=target.stat().st_mtime_ns
        identical=self.b.action(selected,'export',True)
        self.assertTrue(identical['ok'],identical);self.assertEqual(identical.get('actions'),['export','favorite','copy-color'])
        self.assertEqual(target.stat().st_mtime_ns,stamp)
        self.assertFalse(self.b.action('restore:last','restore',True)['ok'])
        # An identical existing file must not consume a different owned receipt.
        other=self.image('blue.png',(0,0,255));other_id='palette:'+self.b.token(other)+':css'
        self.assertTrue(self.b.action(other_id,'export',True)['ok'])
        previous=self.b.journal();identical=self.b.action(selected,'export',True)
        self.assertEqual(identical.get('actions'),['export','favorite','copy-color','restore']);self.assertEqual(self.b.journal(),previous)
        (self.b.export_root()/previous['file']).write_bytes(b'external edit')
        self.assertEqual(self.b.action(selected,'export',True).get('actions'),['export','favorite','copy-color'])
        target.write_bytes(b'other writer')
        failed=self.b.action(selected,'export',True)
        self.assertFalse(failed['ok']);self.assertNotIn('actions',failed)

    def test_unrelated_candidate_race_does_not_block_selected_export(self):
        unrelated=self.image('aaa.png');wanted=self.image('zzz.png',(0,0,255))
        selected='palette:'+self.b.token(wanted)+':json';actual=self.b.image_files
        for replacement in ('missing','symlink','fifo','oversized'):
            with self.subTest(replacement=replacement):
                if unrelated.exists() or unrelated.is_symlink():unrelated.unlink()
                unrelated.write_bytes(png())
                def race():
                    files=actual();unrelated.unlink()
                    if replacement=='symlink':unrelated.symlink_to(wanted)
                    if replacement=='fifo':os.mkfifo(unrelated)
                    if replacement=='oversized':
                        with unrelated.open('wb') as f:f.truncate(self.b.MAX_IMAGE+1)
                    return files
                with patch.object(self.b,'image_files',race):result=self.b.action(selected,'export',True)
                self.assertTrue(result['ok'],result)
                self.assertEqual(json.loads(Path(result['path']).read_text())['colors'],['#0000FF'])

    def test_unpublished_export_keeps_previous_restore_through_failure_or_crash(self):
        self.image('aaa.png');wanted=self.image('bbb.png',(0,0,255))
        rows=self.b.catalog()['rows'];first_id=rows[0]['id']
        selected=next(r['id'] for r in rows if r['preview']==str(wanted) and r['format']=='json')
        first=self.b.action(first_id,'export',True);self.assertTrue(first['ok'],first)
        previous=self.b.journal();target=self.b.export_root()/(selected.split(':')[1]+'.json')
        for boundary in ('save-before','save-after','link-conflict','crash-before-link'):
            with self.subTest(boundary=boundary):
                if target.exists():target.unlink()
                with self.b.State() as state:state.save(previous)
                actual_save=self.b.State.save;actual_link=self.b.os.link
                def save(state,value):
                    if boundary=='save-before':raise OSError('disk full')
                    actual_save(state,value)
                    if boundary=='save-after':raise OSError('receipt directory fsync failed')
                def link(src,dst,**kwargs):
                    if boundary=='link-conflict':target.write_bytes(b'other writer')
                    if boundary=='crash-before-link':os._exit(73)
                    return actual_link(src,dst,**kwargs)
                # A separate process is necessary: no finally/rollback runs after _exit.
                pid=os.fork()
                if pid==0:
                    with patch.object(self.b.State,'save',save),patch.object(self.b.os,'link',link):
                        result=self.b.action(selected,'export',True)
                    os._exit(0 if not result['ok'] else 74)
                _,status=os.waitpid(pid,0)
                self.assertEqual(os.waitstatus_to_exitcode(status),73 if boundary=='crash-before-link' else 0)
                self.assertEqual(self.b.journal(),previous,'failed/unpublished export consumed previous ownership')
                self.assertEqual(Path(first['path']).read_bytes(),self.b.render(['#FF0000'],'json'))
                if boundary=='link-conflict':
                    self.assertEqual(target.read_bytes(),b'other writer');target.unlink()
                else:self.assertFalse(target.exists())
        restored=self.b.action(first_id,'restore',True);self.assertTrue(restored['ok'],restored)

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

    def test_receipt_exact_8192_bytes_is_read_but_8193_is_refused(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json'
        exported=self.b.action(selected,'export',True);self.assertTrue(exported['ok'],exported)
        previous=self.b.journal();target=Path(exported['path']);expected=target.read_bytes()
        pending=dict(previous,file='a'*32+'.css',previous=previous)
        published=dict(previous,previous={})
        for kind,value in (('plain',previous),('unpublished',pending),('published',published)):
            for size in (8192,8193):
                with self.subTest(receipt=kind,size=size):
                    data=json.dumps(value).encode();data+=b' '*(size-len(data));self.assertEqual(len(data),size)
                    with self.b.State() as state:state.put('restore.json',data)
                    receipt_path=self.b.state_path()/'restore.json';inode=receipt_path.stat().st_ino
                    self.assertEqual(self.b.journal(),previous if size==8192 else {})
                    self.assertEqual(self.b.export_actions(),['export','favorite','copy-color','restore'] if size==8192 else ['export','favorite','copy-color'])
                    if size==8193:
                        with patch.object(self.b,'run',wraps=self.b.run) as provider:
                            result=self.b.action('restore:last','restore',True)
                        self.assertFalse(result['ok'],result);provider.assert_not_called()
                    self.assertEqual(receipt_path.read_bytes(),data);self.assertEqual(receipt_path.stat().st_ino,inode)
                    self.assertEqual(target.read_bytes(),expected)
        with self.b.State() as state:state.put('restore.json',json.dumps(pending).encode().ljust(8192,b' '))
        restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
        self.assertEqual(Path(restored['path']).read_bytes(),expected);self.assertFalse(target.exists())

    def test_previous_object_nesting_cannot_authorize_unowned_restore(self):
        source=self.image();selected='palette:'+self.b.token(source)+':json'
        exported=self.b.action(selected,'export',True);self.assertTrue(exported['ok'],exported)
        previous=self.b.journal();target=Path(exported['path']);expected=target.read_bytes()
        nested=dict(previous,metadata={'nested':{'object':{}}})
        pending=dict(previous,file='a'*32+'.css',previous=nested)
        with self.b.State() as state:state.save(pending)
        self.assertEqual(self.b.journal(),nested);self.assertEqual(self.b.export_actions(),['export','favorite','copy-color','restore'])
        prefix=b'{"file":"'+b'a'*32+b'.css","previous":'
        for deep in (prefix+b'{"x":'*1200+b'{}'+b'}'*1200+b'}',prefix+b'{"previous":'*600+b'{}'+b'}'*600+b'}'):
            with self.subTest(receipt_bytes=len(deep)):
                self.assertLess(len(deep),8192,'exercise nested-object fallback within the byte limit')
                with self.b.State() as state:state.put('restore.json',deep)
                receipt_path=self.b.state_path()/'restore.json';inode=receipt_path.stat().st_ino
                # Parsed unknown metadata is not ownership; do not require an arbitrary JSON depth policy.
                resolved=self.b.journal()
                self.assertIsInstance(resolved,dict);self.assertFalse('selected' in resolved or 'file' in resolved)
                self.assertEqual(self.b.export_actions(),['export','favorite','copy-color'])
                with patch.object(self.b,'run',wraps=self.b.run) as provider:
                    result=self.b.action('restore:last','restore',True)
                self.assertFalse(result['ok'],result);provider.assert_not_called()
                self.assertEqual(receipt_path.read_bytes(),deep);self.assertEqual(receipt_path.stat().st_ino,inode)
                self.assertEqual(target.read_bytes(),expected)
        with self.b.State() as state:state.save(pending)
        restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
        self.assertEqual(Path(restored['path']).read_bytes(),expected)

    def test_repeated_interrupted_publications_do_not_nest_pending_receipts(self):
        first=self.image();selected='palette:'+self.b.token(first)+':json'
        exported=self.b.action(selected,'export',True);self.assertTrue(exported['ok'],exported)
        previous=self.b.journal();public={Path(exported['path']):Path(exported['path']).read_bytes()}
        for attempt in range(40):
            with self.subTest(attempt=attempt):
                source=self.image('retry-%02d.png'%attempt,(0,0,255));selected='palette:'+self.b.token(source)+':json'
                target=self.b.export_root()/(selected.split(':')[1]+'.json');published=bool(attempt%2);actual_link=os.link
                pid=os.fork()
                if pid==0:
                    def link(src,dst,**kwargs):
                        if published:actual_link(src,dst,**kwargs)
                        os._exit(73)
                    with patch.object(os,'link',link):self.b.action(selected,'export',True)
                    os._exit(74)
                _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),73)
                raw=(self.b.state_path()/'restore.json').read_bytes();self.assertLessEqual(len(raw),8192)
                pending=json.loads(raw);self.assertEqual(pending['previous'],previous)
                self.assertNotIn('previous',pending['previous'])
                saved=self.b.journal()
                if published:
                    self.assertEqual(saved['selected'],selected);self.assertEqual(saved['inode'],target.stat().st_ino)
                    public[target]=self.b.render(['#0000FF'],'json');previous=saved
                else:
                    self.assertEqual(saved,previous);self.assertFalse(target.exists())
                self.assertEqual(self.b.export_actions(),['export','favorite','copy-color','restore'])
                for path,data in public.items():self.assertEqual(path.read_bytes(),data)
        restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
        detached=self.b.export_root()/previous['file'];self.assertFalse(detached.exists())
        self.assertEqual(Path(restored['path']).read_bytes(),public.pop(detached))
        for path,data in public.items():self.assertEqual(path.read_bytes(),data)

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
        def race(path,**kwargs):
            colors=actual(path,**kwargs);p.write_bytes(png((0,255,0)));return colors
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
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'preferences-config'),'PATH':str(self.home/'bin')+':/usr/bin'},clear=True)
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

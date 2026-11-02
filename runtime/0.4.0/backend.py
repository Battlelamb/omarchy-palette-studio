# SPDX-License-Identifier: GPL-3.0-only
"""Independent bounded local adapter; no daemon, network or shell interpolation."""
import argparse
import contextlib
import colorsys
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))

PREFERENCE_SCHEMA = {
    'density': {'type':'enum','default':'comfortable','choices':['comfortable','compact'],'label':'Density'},
    'layout': {'type':'enum','default':'grid','choices':['grid','list'],'label':'Layout'},
    'sortOrder': {'type':'enum','default':'default','choices':['default','name','favorites-first'],'label':'Sort order'},
    'style': {'type':'enum','default':'vibrant','choices':['vibrant','muted','pastel','monochrome','high-contrast'],'label':'Palette style'},
    'favoritesOnly': {'type':'boolean','default':False,'label':'Favorites only'},
    'colorCount': {'type':'integer','default':6,'minimum':2,'maximum':16,'label':'Maximum colors'},
}

def prefs_module():
    import preferences
    return preferences

def payload_object(raw):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>16384: raise ValueError('Payload exceeds 16384 UTF-8 bytes')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Duplicate payload key')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite payload number')))
    if not isinstance(value,dict): raise ValueError('Payload must be an object')
    return value

def preferences(apply=False,payload=None):
    if payload is None:
        if apply: raise ValueError('Preference writes require a payload')
        prefs=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)
    else:
        if not apply: raise ValueError('Explicit --apply is required')
        if payload=={'reset':True} and type(payload['reset']) is bool: prefs=prefs_module().reset(PLUGIN,PREFERENCE_SCHEMA)
        else: prefs=prefs_module().change(PLUGIN,PREFERENCE_SCHEMA,payload)
    return {'ok':True,'preferences':prefs,'settingsSchema':PREFERENCE_SCHEMA,'message':'Preferences saved.' if apply else 'Saved preferences.'}

STOCK = Path('/usr/share/omarchy/themes')
MAX_ROWS = 256
MAX_FILES = 512
MAX_ENTRIES = 1024
MAX_IMAGE = 32 * 1024 * 1024
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
HEX = re.compile(r'#[0-9a-fA-F]{6}\Z')

def home():
    p = Path(os.environ['HOME'])
    if not p.is_absolute(): raise ValueError('HOME must be absolute')
    return p

def text(value, limit=160):
    return isinstance(value,str) and 0 < len(value) <= limit and not any(ord(c)<32 or ord(c)==127 or 0xd800<=ord(c)<=0xdfff for c in value)

def directory(path, create=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('Unsafe local path')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part,0o700,dir_fd=fd)
                except FileExistsError: pass
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        return fd
    except BaseException:
        os.close(fd);raise

def names(path):
    try:
        fd=directory(path)
        try:
            with os.scandir(fd) as entries:
                out=[e.name for e in itertools.islice(entries,MAX_ENTRIES+1)]
            if len(out)>MAX_ENTRIES: return []
            return sorted(n for n in out if text(n) and not n.startswith('.'))
        finally: os.close(fd)
    except OSError: return []

def is_directory(path):
    try: fd=directory(path);os.close(fd);return True
    except OSError: return False

@contextlib.contextmanager
def opened(path,limit=MAX_IMAGE):
    fd=directory(Path(path).parent)
    try:
        filefd=os.open(Path(path).name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info=os.fstat(filefd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>limit: raise ValueError('File is not regular or exceeds size limit')
            with os.fdopen(filefd,'rb',closefd=False) as stream: yield stream,info
        finally: os.close(filefd)
    finally: os.close(fd)

def fingerprint(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def read(path,limit=65536):
    with opened(path,limit) as (stream,before):
        data=stream.read(limit+1)
        if len(data)>limit or fingerprint(os.fstat(stream.fileno()))!=fingerprint(before): raise ValueError('File changed while reading')
        return data

def optional_read(path,limit=65536):
    try: return read(path,limit)
    except (OSError,ValueError): return b''

def themes():
    result={}
    user=home()/'.config/omarchy/themes'
    for root in (STOCK,user):
        for name in names(root):
            if SLUG.fullmatch(name) and is_directory(root/name): result.setdefault(name,[]).append(root/name)
    # A user symlink would still be followed by the stock setter: block its stock slug too.
    for name in list(result):
        p=user/name
        if os.path.lexists(p) and not is_directory(p): del result[name]
    return dict(sorted(result.items()))

def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24:
        width,height=struct.unpack('!II',data[16:24])
        if width and height and width<=16384 and height<=16384 and width*height<=64000000: return 'PNG'
    if data.startswith(b'\xff\xd8\xff'): return 'JPEG'
    if len(data)>12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'WEBP'
    if data.startswith(b'BM'): return 'BMP'
    raise ValueError('Unsupported or oversized image header')

def valid_image(path):
    if not text(str(path),4096) or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.bmp'): return False
    try:
        with opened(path) as (stream,info): image_type(stream.read(64))
        return True
    except (OSError,ValueError): return False

def image_roots():
    roots=[]
    for base in (home()/'Pictures/Wallpapers',home()/'.config/omarchy/backgrounds'):
        roots.append(base)
        roots.extend(base/name for name in names(base) if is_directory(base/name))
    roots += [p/'backgrounds' for paths in themes().values() for p in paths]
    roots.append(home()/'.local/state/omarchy/current/theme/backgrounds')
    return roots[:MAX_FILES]

def image_files():
    result=[];seen=set()
    for root in image_roots():
        for name in names(root):
            p=root/name
            if p not in seen and valid_image(p):
                result.append(p);seen.add(p)
                if len(result)>=MAX_FILES: return result
    return result

def token(p):
    with opened(p) as (_,info): stamp=fingerprint(info)
    return hashlib.sha256((str(p)+repr(stamp)).encode()).hexdigest()[:32]

MAGICK='/usr/bin/magick'

def palette_token(path,options):
    identity=token(path)
    if options['style']=='vibrant' and options['colorCount']==6: return identity
    return hashlib.sha256((identity+':'+options['style']+':'+str(options['colorCount'])).encode()).hexdigest()[:32]

def style_colors(colors,style):
    if style=='vibrant': return colors
    result=[]
    for index,color in enumerate(colors):
        rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        hue,light,saturation=colorsys.rgb_to_hls(*rgb)
        if style=='muted': saturation=min(saturation,0.35)
        elif style=='pastel': light=0.82;saturation=min(saturation,0.55)
        elif style=='monochrome': saturation=0
        elif style=='high-contrast': light=0.08 if index%2==0 else 0.92;saturation=min(saturation,0.65)
        else: raise OperationError('Unknown palette style')
        value='#'+''.join(f'{round(c*255):02X}' for c in colorsys.hls_to_rgb(hue,light,saturation))
        if value not in result: result.append(value)
    return result

def extract(p,timeout=3,options=None):
    options=options if options is not None else prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)['options']
    count=options['colorCount']
    data=read(p,MAX_IMAGE);coder=image_type(data)
    argv=[MAGICK,'-limit','thread','1','-limit','memory','128MiB','-limit','map','128MiB','-limit','disk','0','-limit','time','2','-limit','width','16384','-limit','height','16384',coder+':-[0]','-resize','64x64!','-background','#000000','-alpha','remove','-alpha','off','-dither','None','-colors',str(count),'-depth','8','-unique-colors','txt:-']
    env={'PATH':'/usr/bin','HOME':'/nonexistent','LANG':'C.UTF-8','MAGICK_CONFIGURE_PATH':'/etc/ImageMagick-7','MAGICK_THREAD_LIMIT':'1'}
    code,out,err=run(argv,payload=data,timeout=timeout,env=env)
    if code or err: raise OperationError('ImageMagick could not safely decode the selected image')
    colors=list(dict.fromkeys(c.upper() for c in re.findall(r'#[0-9A-Fa-f]{6}(?![0-9A-Fa-f])',out.decode(errors='replace'))))
    if not colors or len(colors)>count: raise OperationError('Extractor produced no usable bounded palette')
    return style_colors(colors,options['style'])

def contrast_ratio(first,second):
    def luminance(color):
        if not isinstance(color,str) or not HEX.fullmatch(color): raise ValueError('Contrast needs #RRGGBB colors')
        channels=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        return sum(weight*(c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4) for c,weight in zip(channels,(0.2126,0.7152,0.0722)))
    a,b=luminance(first),luminance(second)
    return round((max(a,b)+0.05)/(min(a,b)+0.05),2)

def contrast_metrics(colors):
    return {'againstBlack':[contrast_ratio(c,'#000000') for c in colors],
            'againstWhite':[contrast_ratio(c,'#FFFFFF') for c in colors],
            'pairs':[{'a':i,'b':j,'ratio':contrast_ratio(a,b)} for i,a in enumerate(colors) for j,b in enumerate(colors) if i<j]}

def catalog():
    rows=[];skipped=0;deadline=time.monotonic()+5
    prefs=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)
    if not os.access(MAGICK,os.X_OK):
        return {'rows':[],'preferences':prefs,'settingsSchema':PREFERENCE_SCHEMA,'message':'ImageMagick extractor unavailable; no packages are installed automatically.','actions':export_actions(),'actionLabel':'Export'}
    files=image_files()
    for p in files[:32]:
        remaining=deadline-time.monotonic()
        if remaining<=0: skipped+=1;break
        try:
            identity=palette_token(p,prefs['options']);colors=extract(p,min(remaining,2),prefs['options'])
            if palette_token(p,prefs['options'])!=identity: raise OperationError('Image changed')
        except (OSError,ValueError,subprocess.SubprocessError): skipped+=1;continue
        contrast=contrast_metrics(colors)
        detail=' '.join(colors)+f" · Contrast first/black {contrast['againstBlack'][0]:.2f}:1; first/white {contrast['againstWhite'][0]:.2f}:1"
        if contrast['pairs']: detail+=f"; minimum pair {min(pair['ratio'] for pair in contrast['pairs']):.2f}:1"
        for fmt in ('json','css','gpl'):
            selected='palette:'+identity+':'+fmt;favorite=selected in prefs['favorites']
            if not prefs['options']['favoritesOnly'] or favorite: rows.append({'id':selected,'label':(p.name[:140]+' · '+fmt.upper()),'detail':detail,'color':colors[0],'preview':str(p),'format':fmt,'favorite':favorite,'category':prefs['options']['style'],'swatches':colors,'contrast':contrast})
    message='Preview colors from local wallpaper bytes. Select a JSON, CSS or GPL row to export only that format; no retint.' if rows else 'No extractable local wallpapers; add PNG/JPEG/WebP/BMP images to Pictures/Wallpapers.'
    if skipped or len(files)>32: message+=' Scan/extraction budget reached or unreadable images skipped; at most 32 wallpaper previews.'
    return {'rows':rows,'preferences':prefs,'settingsSchema':PREFERENCE_SCHEMA,'message':message,'actions':export_actions(),'actionLabel':'Export'}


class OperationError(ValueError): pass

def run(argv, payload=None, timeout=12, env=None):
    """Bound both collection and process group lifetime, including pipe-holding children."""
    source=None
    if payload is not None:
        source=os.fdopen(os.memfd_create('palette-input',os.MFD_CLOEXEC),'w+b')
        source.write(payload);source.seek(0)
    try:
        proc=subprocess.Popen(argv,stdin=source if source else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env=env)
    finally:
        if source: source.close()
    complete=False
    output=[bytearray(),bytearray()];deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            for index,pipe in enumerate((proc.stdout,proc.stderr)):
                os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,index)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise OperationError('Provider timed out; state may be partially changed. Refresh before retrying.')
                for key,_ in selector.select(min(remaining,0.1)):
                    block=os.read(key.fd,8192)
                    if not block: selector.unregister(key.fileobj);continue
                    output[key.data].extend(block)
                    if len(output[key.data])>65536: raise OperationError('Provider output exceeded limit; state may be partially changed.')
            try: code=proc.wait(timeout=max(0.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: raise OperationError('Provider timed out; state may be partially changed.') from None
        complete=True
        return code,bytes(output[0]),bytes(output[1])
    finally:
        # Do not leave descendants holding an interrupted operation alive.
        if not complete or proc.poll() is None:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        for pipe in (proc.stdout,proc.stderr): pipe.close()
        proc.wait()

def state_path(): return home()/'.local/state'/('omarchy-'+PLUGIN)

class State:
    def __enter__(self):
        self.fd=directory(state_path(),True)
        try:
            self.lock=os.open('lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=self.fd)
            if not stat.S_ISREG(os.fstat(self.lock).st_mode) or os.fstat(self.lock).st_nlink!=1: raise OperationError('Unsafe action lock')
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            if hasattr(self,'lock'): os.close(self.lock)
            os.close(self.fd);raise
        return self
    def __exit__(self,*_):
        os.close(self.lock);os.close(self.fd)
    def put(self,name,data):
        temp='.write-'+os.urandom(12).hex()
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        try:
            with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,name,src_dir_fd=self.fd,dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try: os.unlink(temp,dir_fd=self.fd)
            except FileNotFoundError: pass
    def save(self,value): self.put('restore.json',json.dumps(value).encode())
    def clear(self):
        try: os.unlink('restore.json',dir_fd=self.fd)
        except FileNotFoundError: pass

def journal():
    try:
        value=json.loads(read(state_path()/'restore.json',8192))
        if not isinstance(value,dict): return {}
        if 'previous' in value:
            previous=value.pop('previous')
            if not isinstance(previous,dict): return {}
            name=value.get('file')
            if not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{32}\.(json|css|gpl)',name): return {}
            try:
                with opened(export_root()/name) as (_,info):
                    published=(info.st_dev,info.st_ino)==(value.get('device'),value.get('inode'))
            except (OSError,ValueError): published=False
            if not published: return previous
        return value
    except (OSError,ValueError): return {}

PLUGIN='palette-studio'
FORMATS=('json','css','gpl')

def export_root(): return home()/'.local/share/omarchy-palette-studio/exports'

def export_actions():
    saved=journal();name=saved.get('file')
    if isinstance(name,str) and re.fullmatch(r'[a-f0-9]{32}\.(json|css|gpl)',name):
        try:
            target=export_root()/name
            if hashlib.sha256(read(target)).hexdigest()==saved.get('sha256'):
                with opened(target) as (_,info):
                    if (info.st_dev,info.st_ino)==(saved.get('device'),saved.get('inode')): return ['export','favorite','copy-color','copy-css','restore']
        except (OSError,ValueError): pass
    return ['export','favorite','copy-color','copy-css']

def render(colors,fmt):
    if not colors or len(colors)>16 or not all(isinstance(c,str) and HEX.fullmatch(c) for c in colors): raise OperationError('Extractor returned invalid colors')
    if fmt=='json': return (json.dumps({'schemaVersion':1,'colors':colors},indent=2)+'\n').encode()
    if fmt=='css': return (':root {\n'+''.join('  --palette-%d: %s;\n'%(i+1,c) for i,c in enumerate(colors))+'}\n').encode()
    if fmt=='gpl':
        lines=['GIMP Palette','Name: Wallpaper palette','Columns: 6','# Generated locally; no app configuration changed']
        lines += ['%3d %3d %3d Color %d'%(*[int(c[n:n+2],16) for n in (1,3,5)],i+1) for i,c in enumerate(colors)]
        return ('\n'.join(lines)+'\n').encode()
    raise OperationError('Unsupported export format')

def perform(selected,verb):
    with State() as state:
        saved=journal()
        if verb=='restore':
            if not saved or selected not in (saved.get('selected'),'restore:last'): raise OperationError('No matching export receipt')
            name=saved.get('file','')
            if not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{32}\.(json|css|gpl)',name): raise OperationError('Invalid export receipt')
            target=export_root()/name
            if hashlib.sha256(read(target)).hexdigest()!=saved.get('sha256'): raise OperationError('Export changed externally; restore refused')
            with opened(target) as (_,info):
                if (info.st_dev,info.st_ino)!=(saved.get('device'),saved.get('inode')): raise OperationError('Export was replaced externally; restore refused')
            fd=directory(export_root());quarantine='.restore-'+os.urandom(12).hex()
            try:
                os.rename(name,quarantine,src_dir_fd=fd,dst_dir_fd=fd)
                try:
                    if hashlib.sha256(read(export_root()/quarantine)).hexdigest()!=saved.get('sha256'): raise OperationError('Export changed during restore; preserved external data')
                    with opened(export_root()/quarantine) as (_,info):
                        if (info.st_dev,info.st_ino)!=(saved.get('device'),saved.get('inode')): raise OperationError('Export replaced during restore; preserved external data')
                    # An editor may still hold this inode open after the hash check.
                    os.fsync(fd)
                except BaseException:
                    # Put back without replacing a concurrent editor's new public file.
                    try: os.link(quarantine,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                    except FileExistsError: raise OperationError('Concurrent export edit; recovery file retained in exports directory') from None
                    else: os.unlink(quarantine,dir_fd=fd)
                    raise
            finally: os.close(fd)
            state.clear()
            recovery=str(export_root()/quarantine)
            return {'ok':True,'path':recovery,'message':'Export detached; recovery file retained at '+recovery+' because open editors may still change it. Other files untouched.'}
        if verb!='export': raise OperationError('Palette Studio supports export, not desktop apply')
        options=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)['options']
        parts=selected.split(':')
        if len(parts)!=3 or parts[0]!='palette' or parts[2] not in FORMATS: raise OperationError('Unknown palette selection')
        p=None
        for candidate in image_files():
            try: identity=palette_token(candidate,options)
            except (OSError,ValueError): continue
            if identity==parts[1]: p=candidate;break
        if p is None: raise OperationError('Wallpaper changed or disappeared; refresh')
        colors=extract(p,options=options)
        if palette_token(p,options)!=parts[1]: raise OperationError('Wallpaper changed during extraction; refresh')
        data=render(colors,parts[2]);name=parts[1]+'.'+parts[2];target=export_root()/name
        fd=directory(export_root(),True)
        try:
            if os.path.lexists(target):
                if read(target)!=data: raise OperationError('Export destination already exists with different data; refusing overwrite')
                return {'ok':True,'path':str(target),'message':'Identical export already exists; not overwritten.'}
            temporary='.export-'+os.urandom(12).hex()
            filefd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            try:
                with os.fdopen(filefd,'wb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno());info=os.fstat(stream.fileno())
                receipt={'selected':selected,'file':name,'sha256':hashlib.sha256(data).hexdigest(),'device':info.st_dev,'inode':info.st_ino}
                # Retain old authority across crashes until the exclusive link publishes this inode.
                state.save(dict(receipt,previous=saved))
                os.link(temporary,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
                os.fsync(fd)
                state.save(receipt)
            finally: os.unlink(temporary,dir_fd=fd)
        finally: os.close(fd)
        if read(target)!=data: raise OperationError('Export readback failed; inspect the destination')
        return {'ok':True,'path':str(target),'message':'Exported '+parts[2].upper()+' to '+str(target)+'; no app configuration changed.'}

def palette_source(selected,options):
    parts=selected.split(':')
    if len(parts)!=3 or parts[0]!='palette' or parts[2] not in FORMATS: raise OperationError('Unknown palette selection')
    for path in image_files():
        try: identity=palette_token(path,options)
        except (OSError,ValueError): continue
        if identity==parts[1]: return path
    raise OperationError('Wallpaper changed or disappeared; refresh')

def remember_success(result,selected):
    try: result['preferences']=prefs_module().remember(PLUGIN,PREFERENCE_SCHEMA,selected)
    except (OSError,ValueError): result['message']+=' Warning: recent selection could not be saved.'
    return result

def action(selected,verb,apply=False,payload=None):
    if not apply: return {'ok':False,'message':'Explicit --apply is required; nothing changed.'}
    if payload is not None and verb!='copy-color': return {'ok':False,'message':'This action does not accept a payload'}
    if not text(selected,160) or verb not in ('apply','restore','export','favorite','copy-color','copy-css'): return {'ok':False,'message':'Invalid selection or action'}
    try:
        if verb in ('favorite','copy-color','copy-css'):
            options=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)['options']
            path=palette_source(selected,options)
        if verb=='favorite':
            return {'ok':True,'message':'Favorite updated.','preferences':prefs_module().toggle(PLUGIN,PREFERENCE_SCHEMA,selected)}
        if verb=='copy-css':
            colors=extract(path,options=options)
            if palette_token(path,options)!=selected.split(':')[1]: raise OperationError('Wallpaper changed during extraction; refresh')
            code,out,err=run(['wl-copy','--type','text/plain;charset=utf-8'],payload=render(colors,'css'),timeout=3)
            if code or err: raise OperationError('Clipboard helper did not confirm acceptance')
            return remember_success({'ok':True,'message':'Clipboard helper accepted CSS palette; no export created.'},selected)
        if verb=='copy-color':
            if not isinstance(payload,dict) or set(payload)!={'index'} or type(payload['index']) is not int: raise OperationError('Copy color requires an integer zero-based index')
            colors=extract(path,options=options)
            if palette_token(path,options)!=selected.split(':')[1]: raise OperationError('Wallpaper changed during extraction; refresh')
            if not 0<=payload['index']<len(colors): raise OperationError('Color index is outside the extracted palette')
            color=colors[payload['index']]
            code,out,err=run(['wl-copy','--type','text/plain;charset=utf-8'],payload=color.encode(),timeout=3)
            if code or err: raise OperationError('Clipboard helper did not confirm acceptance')
            return remember_success({'ok':True,'message':'Clipboard helper accepted '+color+'.','color':color},selected)
        result=perform(selected,verb)
        result['actions']=export_actions()
        return remember_success(result,selected) if verb=='export' else result
    except OperationError as error: return {'ok':False,'message':str(error)[:600]}
    except (OSError,ValueError,KeyError,subprocess.SubprocessError): return {'ok':False,'message':'Local operation failed; check provider, permissions and safe roots. Partial state may remain; refresh.'}


class Parser(argparse.ArgumentParser):
    def error(self,message): raise ValueError('Invalid command arguments; use catalog or action --id ID --verb VERB --apply')

def main():
    try:
        parser=Parser(add_help=False,allow_abbrev=False)
        parser.add_argument('command',choices=('catalog','action','preferences'))
        parser.add_argument('--id')
        parser.add_argument('--verb')
        parser.add_argument('--payload')
        parser.add_argument('--apply',action='store_true')
        args=parser.parse_args()
        if args.command=='catalog':
            if args.id is not None or args.verb is not None or args.apply or args.payload is not None: raise ValueError('Catalog takes no options')
            value=catalog()
        elif args.command=='preferences':
            if args.id is not None or args.verb is not None: raise ValueError('Unexpected preference options')
            value=preferences(args.apply,payload_object(args.payload) if args.payload is not None else None)
        else:
            if args.id is None or args.verb is None: raise ValueError('Action needs --id and --verb')
            value=action(args.id,args.verb,args.apply,payload_object(args.payload) if args.payload is not None else None)
        raw=prefs_module().encode_reply(value)
        print(raw)
        return 0 if args.command=='catalog' or value.get('ok') is True else 1
    except (OSError,ValueError,KeyError,RecursionError,subprocess.SubprocessError):
        print(prefs_module().encode_reply({'ok':False,'message':'Local operation failed; check provider availability, file permissions and refresh.'}))
        return 1

if __name__=='__main__': sys.exit(main())

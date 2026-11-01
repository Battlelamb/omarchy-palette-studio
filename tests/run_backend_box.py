#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run only backend tests in a private offline namespace; never use live HOME/IPC."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

repo=Path(__file__).resolve().parents[1]
label=sys.argv[1] if len(sys.argv)>1 else 'backend-final'
if not re.fullmatch(r'[a-z0-9-]{1,64}',label): raise SystemExit('Invalid receipt label')
output=repo/'release-output';output.mkdir(exist_ok=True)
receipt=output/(label+'.json');log=output/(label+'.txt')
if receipt.exists() or log.exists(): raise SystemExit('Receipt already exists; choose a new label')
if not os.environ.get('TMPDIR'): raise SystemExit('Set TMPDIR to an approved scratch directory')
with tempfile.TemporaryDirectory(prefix='backend-box-',dir=os.environ['TMPDIR']) as scratch:
    root=Path(scratch);(root/'stock').mkdir()
    deny=root/'refuse';deny.write_text('#!/usr/bin/python3\nimport sys\nprint("Production setter blocked: tests must provide a private shim",file=sys.stderr)\nsys.exit(126)\n');deny.chmod(0o755)
    command=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--ro-bind','/usr','/usr','--ro-bind','/etc/ImageMagick-7','/etc/ImageMagick-7','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--tmpfs','/run','--tmpfs','/home','--dir','/home/test','--ro-bind',str(repo),'/src','--ro-bind',str(root/'stock'),'/usr/share/omarchy/themes']
    targets={Path(directory,name).resolve() for directory in ('/usr/bin','/usr/share/omarchy/bin') for name in ('omarchy-theme-set','omarchy-theme-bg-set','omarchy-shell') if Path(directory,name).exists()}
    for target in sorted(targets): command+=['--ro-bind',str(deny),str(target)]
    command+=['--setenv','HOME','/home/test','--setenv','PATH','/usr/bin','--setenv','LANG','C.UTF-8','--setenv','TMPDIR','/tmp','--setenv','PYTHONDONTWRITEBYTECODE','1','--chdir','/src','/usr/bin/env','-u','PYTHONPATH','/usr/bin/python3','-m','unittest','discover','-s','tests','-p','test_*.py','-v']
    start=time.monotonic()
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    raw=result.stdout.decode(errors='replace');log.write_text(raw)
    count=re.search(r'^Ran (\d+) tests? in ',raw,re.M)
    value={'command':command,'exitCode':result.returncode,'testCount':int(count[1]) if count else 0,'durationSeconds':round(time.monotonic()-start,3),'productionSettersMasked':True,'offline':True,'fakeHome':True,'liveIPC':False}
    receipt.write_text(json.dumps(value,indent=2)+'\n')
    print(raw);print(json.dumps(value))
    sys.exit(result.returncode or (0 if count and re.search(r'^OK$',raw,re.M) else 1))

"""Transaction tests run the actual embedded installer against isolated paths."""
import io,json,os,shutil,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

class WindowsTransactionTests(unittest.TestCase):
    def test_install_then_rollback_preserves_state_and_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);app=base/'telegram-stl';(app/'data').mkdir(parents=True);(app/'downloads').mkdir()
            (app/'VERSION').write_text('0.1.10');(app/'data/history').write_bytes(b'private-state');(app/'downloads/file.zip').write_bytes(b'download')
            payload=base/'runtime.tar.gz'
            with tarfile.open(payload,'w:gz') as archive:
                for name,content in [('VERSION',b'0.1.13'),('runtime/python/bin/python3',b'fake')]:
                    info=tarfile.TarInfo('telegram-stl/'+name);info.size=len(content);archive.addfile(info,io.BytesIO(content))
            source=(Path(__file__).resolve().parents[1]/'windows/update.sh').read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
            source=source.replace('/home/stl/telegram-stl',str(app)).replace('/home/stl/.local/share/telegram-stl-tdl',str(base/'session'))
            import hashlib
            checksum=hashlib.sha256(payload.read_bytes()).hexdigest()
            with patch.object(sys,'argv',['update','install',str(payload),checksum]),patch('subprocess.run'):
                exec(compile(source,'update.sh','exec'),{})
            self.assertEqual((app/'VERSION').read_text(),'0.1.13');self.assertEqual((app/'data/history').read_bytes(),b'private-state')
            self.assertEqual((app/'downloads/file.zip').read_bytes(),b'download')
            (app/'data/history').write_bytes(b'changed-by-migration')
            with patch.object(sys,'argv',['update','rollback']),patch('subprocess.run'),self.assertRaises(SystemExit):
                exec(compile(source,'update.sh','exec'),{})
            self.assertEqual((app/'VERSION').read_text(),'0.1.10');self.assertEqual((app/'data/history').read_bytes(),b'private-state')
            self.assertEqual((app/'downloads/file.zip').read_bytes(),b'download')
            self.assertFalse((app/'data/update-installing').exists())

    def test_recover_interrupted_directory_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);app=base/'telegram-stl';backup=base/'backup';old=backup/'application';old.mkdir(parents=True)
            (old/'VERSION').write_text('0.1.10')
            staged=backup/'new/telegram-stl';(staged/'downloads').mkdir(parents=True)
            (staged/'downloads/file.zip').write_bytes(b'keep download')
            with tarfile.open(backup/'private-data.tar.gz','w:gz') as archive:
                info=tarfile.TarInfo('data/history');info.size=5;archive.addfile(info,io.BytesIO(b'saved'))
            (base/'telegram-stl-update-state.json').write_text(json.dumps({'backup':str(backup),'phase':'switching','time':0}))
            source=(Path(__file__).resolve().parents[1]/'windows/update.sh').read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0].replace('/home/stl/telegram-stl',str(app)).replace('/home/stl/.local/share/telegram-stl-tdl',str(base/'session'))
            with patch.object(sys,'argv',['update','recover']),patch('subprocess.run'),self.assertRaises(SystemExit):exec(compile(source,'update.sh','exec'),{})
            self.assertEqual((app/'data/history').read_bytes(),b'saved')
            self.assertEqual((app/'downloads/file.zip').read_bytes(),b'keep download')

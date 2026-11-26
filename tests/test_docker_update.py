import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('docker_helper',Path(__file__).resolve().parents[1]/'docker/update-helper.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class DockerPackageTests(unittest.TestCase):
    def make(self,path,extra=None,version='0.2.9'):
        with zipfile.ZipFile(path,'w') as z:
            for name in ('Start.command','Stop.command','Update.command','compose.yaml','README.md','support/app/VERSION','support/Dockerfile','support/update-helper.py'):
                z.writestr('Telegram-STL-Manager-Docker/'+name,version if name.endswith('VERSION') else 'test')
            if extra:z.writestr(*extra)
    def test_verified_layout_extracts_and_rejects_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'a.zip';self.make(archive)
            helper.unpack(archive,root/'out','0.2.9')
            self.assertEqual((root/'out/support/app/VERSION').read_text(),'0.2.9')
            with self.assertRaisesRegex(ValueError,'version'):helper.unpack(archive,root/'bad','0.3.0')
            self.assertFalse((root/'bad').exists())
    def test_traversal_and_symlinks_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'a.zip'
            for name in ('../outside','support/../../outside','/absolute','support\\outside'):
                self.make(archive,('Telegram-STL-Manager-Docker/'+name,'bad'))
                with self.assertRaises(ValueError):helper.unpack(archive,root/'out','0.2.9')
                self.assertFalse((root/'out').exists())
            link=zipfile.ZipInfo('Telegram-STL-Manager-Docker/support/link');link.external_attr=(0o120777<<16)
            self.make(archive,(link,'/etc/passwd'))
            with self.assertRaisesRegex(ValueError,'symlink'):helper.unpack(archive,root/'out','0.2.9')
    def test_host_controls_docker_without_socket_or_privileged_container(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'docker/Update.command').read_text()
        self.assertNotIn('/var/run/docker.sock',script)
        self.assertNotIn('--privileged',script)
        self.assertIn('$old_volume:/source:ro',script)
        self.assertIn('helper reserve',script)
        self.assertLess(script.index('docker build'),script.index('docker stop'))
        self.assertIn('--recover',script)
        self.assertIn('set -Eeuo pipefail',script)

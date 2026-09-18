"""Private local IPC; the CLI service owns the login and independent job contexts."""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time


class ServiceError(RuntimeError):pass


class TelegramService:
    def __init__(self, root, state, command):
        self.root=Path(root);self.state=Path(state);self.command=command
        self.socket=self.state/'service.sock'
        self.source=str((self.root/'data/source.json').resolve())

    def connect(self):
        conn=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);conn.settimeout(1)
        try:conn.connect(str(self.socket));return conn
        except OSError:conn.close();return None

    def healthy(self):
        conn=self.connect()
        if conn is None:return False
        with conn:
            conn.sendall(json.dumps({'source':self.source,'args':['health']}).encode()+b'\n')
            result=b''
            while b'\n' not in result:
                part=conn.recv(4096)
                if not part or len(result)>16384:raise ServiceError('Invalid Telegram service response.')
                result+=part
            self.validate(json.loads(result))
            return True

    def validate(self, response):
        if response.get('protocol')!=1 or response.get('source')!=self.source:
            raise ServiceError('The Telegram service belongs to a different application configuration.')
        if not response.get('ok'):raise ServiceError(response.get('error') or 'Telegram operation failed.')

    def ensure(self, stopped, waiting):
        if self.healthy():return
        with (self.state/'service-start.lock').open('a') as startup:
            while True:
                if stopped():raise ServiceError('Stopped while starting the Telegram connection.')
                if self.healthy():return
                try:fcntl.flock(startup,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:waiting();time.sleep(.2)
            if self.healthy():return
            # During an upgrade, let an existing standalone command finish.
            # Subsequent CLI commands proxy into the service, so the lock is
            # released after startup and is never held for a service job.
            with (self.state/'application.lock').open('a') as legacy:
                last=0
                while True:
                    if stopped():raise ServiceError('Stopped while starting the Telegram connection.')
                    try:fcntl.flock(legacy,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()-last>=1:waiting();last=time.monotonic()
                        time.sleep(.2)
                if self.healthy():return
                with (self.state/'service.log').open('ab') as log:
                    child=subprocess.Popen(self.command('stl','serve'),cwd=self.state,stdin=subprocess.DEVNULL,
                                           stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                deadline=time.monotonic()+60
                while True:
                    if self.healthy():return
                    if child.poll() is not None:raise ServiceError('Telegram service could not start. Check the connection or CLI login.')
                    if stopped() or time.monotonic()>deadline:
                        # This is our unready startup only, not a running shared
                        # service. No other command has yet been accepted.
                        child.send_signal(2)
                        try:child.wait(timeout=5)
                        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                        raise ServiceError('Telegram service startup stopped or timed out.')
                    time.sleep(.2)

    def run(self, args, stopped, tick, timeout, waiting):
        self.ensure(stopped,waiting)
        conn=self.connect()
        if conn is None:
            # An idle service may exit between its health response and connect.
            self.ensure(stopped,waiting);conn=self.connect()
        if conn is None:raise ServiceError('Telegram service is unavailable; retry the operation.')
        with conn:
            accepted=False
            try:
                if stopped():raise ServiceError('Stopped before the Telegram operation started.')
                conn.sendall(json.dumps({'source':self.source,'args':[str(a) for a in args[1:]]}).encode()+b'\n')
                accepted=True;conn.settimeout(.2);started=time.monotonic();result=b''
                while b'\n' not in result:
                    if stopped():raise ServiceError('Stopped by request; staged data retained.')
                    if time.monotonic()-started>timeout:raise ServiceError('Telegram operation timed out; retry the manual run.')
                    tick()
                    try:part=conn.recv(65536)
                    except socket.timeout:continue
                    if not part:raise ServiceError('Telegram service disconnected; staged data retained.')
                    result+=part
                    if len(result)>1024*1024:raise ServiceError('Invalid Telegram service response.')
                tick();self.validate(json.loads(result))
            except BaseException:
                if accepted:
                    # Half-close asks the server to cancel and still allows its
                    # final response, after it has closed this job's files.
                    try:
                        conn.shutdown(socket.SHUT_WR);conn.settimeout(10)
                        conn.recv(1024*1024)
                    except OSError:pass
                raise

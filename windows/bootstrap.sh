#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends cifs-utils sudo python3 util-linux iproute2 ca-certificates zip tzdata
id stl >/dev/null 2>&1 || useradd --create-home --shell /bin/bash stl
install -d -o stl -g stl /home/stl/telegram-stl
# The application payload and bootstrap scripts are installer-owned files.
tar -xzf "$1" -C /home/stl/telegram-stl --strip-components=1
chown -R stl:stl /home/stl/telegram-stl
install -Dm755 /home/stl/telegram-stl/windows/share-helper.py /usr/local/lib/telegram-stl/share-helper.py
printf 'stl ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/lib/telegram-stl/share-helper.py\n' >/etc/sudoers.d/telegram-stl-share
chmod 440 /etc/sudoers.d/telegram-stl-share
visudo -cf /etc/sudoers.d/telegram-stl-share
printf '[user]\ndefault=stl\n' >/etc/wsl.conf
printf 'TelegramSTL installer v1\n' >/etc/telegram-stl-managed
runuser -u stl -- /home/stl/telegram-stl/runtime/python/bin/python3 /home/stl/telegram-stl/tools/init-local.py

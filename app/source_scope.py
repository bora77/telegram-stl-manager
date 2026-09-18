"""One explicitly configured Telegram source; local values are never distributed."""
from dataclasses import dataclass
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SourceScope:
    chat_id: int
    toc_message_id: int

    def __post_init__(self):
        if (type(self.toc_message_id) is not int or not 0 < self.toc_message_id <= 2147483647
                or type(self.chat_id) is not int or not 0 < self.chat_id <= 2**53 - 1):
            raise ValueError('Configure a positive chat ID and TOC message ID in data/source.json.')

    @property
    def prefix(self):
        return f'https://t.me/c/{self.chat_id}/'

    @property
    def home_url(self):
        return self.prefix + str(self.toc_message_id)

    def topic_id(self, url):
        match = re.fullmatch(re.escape(self.prefix) + r'([1-9]\d*)', url) if isinstance(url, str) else None
        return int(match[1]) if match and int(match[1]) <= 2147483647 else None


def load_source(root=ROOT):
    path = Path(root) / 'data/source.json'
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError('Set up the private data/source.json before using Telegram content actions.') from error
    if not isinstance(data, dict) or set(data) != {'chat_id', 'toc_message_id'}:
        raise ValueError('data/source.json must contain only chat_id and toc_message_id.')
    return SourceScope(**data)

"""Synthetic local fixtures. These identifiers do not configure a live source."""
import json
from pathlib import Path
from source_scope import SourceScope

TEST_SOURCE = SourceScope(chat_id=123456789, toc_message_id=100)


def configure_source(root):
    path = Path(root) / 'data/source.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'chat_id': TEST_SOURCE.chat_id,
                               'toc_message_id': TEST_SOURCE.toc_message_id}))
    return root

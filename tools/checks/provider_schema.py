"""The JSON schema of the messages of the socket of a provider (contracts/provider_socket.md): the models of the wire and of the
table, made into one document, `contracts/provider_socket.schema.json`. The document is for whoever writes a provider; the models
are the source, and a document that differs from them is stale.

  provider_schema.py           writes the document
  provider_schema.py --check   says whether the document is the one the models make (SCHEMA_OK or SCHEMA_STALE)"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.contracts import TableRow, TitleCard, TitleLine, TitleSearchReply, TitleSearchRequest, UnreadPart  # noqa: E402
from core.contracts.provider import (PROTOCOL, AdoptItem, AdoptReply, AdoptRequest, DownloadFrame, DownloadRequest,  # noqa: E402
                                     FeedEvent, Manifest, ReadFrame, ReadRequest)
from modules.sources.sites import TableDraft  # noqa: E402

PATH = os.path.join(ROOT, "contracts", "provider_socket.schema.json")
MESSAGES = (Manifest, FeedEvent, TitleSearchRequest, TitleSearchReply, TitleCard, TitleLine, ReadRequest, ReadFrame, TableDraft,
            TableRow, UnreadPart, DownloadRequest, DownloadFrame, AdoptRequest, AdoptReply, AdoptItem)


def document() -> dict:
    return {"protocol": PROTOCOL, "messages": {each.__name__: each.model_json_schema() for each in MESSAGES}}


def text() -> str:
    return json.dumps(document(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main() -> None:
    if "--check" in sys.argv:
        with open(PATH, encoding="utf-8") as stored:
            print("SCHEMA_OK" if stored.read() == text() else "SCHEMA_STALE")
        return
    with open(PATH, "w", encoding="utf-8") as out:
        out.write(text())
    print(f"written {PATH}")
    print("SCHEMA_WRITTEN")


if __name__ == "__main__":
    main()

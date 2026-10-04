"""Repeatable bounded readers for gateway CSV and legacy JSON-array checkpoints."""
import csv
import json
from pathlib import Path

RECORD_LIMIT=2*1024*1024


def csv_records(path):
    with Path(path).open('r',encoding='utf-8-sig',newline='') as handle:
        for number,row in enumerate(csv.reader(handle),1):
            if len(row)!=1:raise ValueError('report row %s is not single-column JSON'%number)
            if len(row[0])>RECORD_LIMIT:raise ValueError('report record limit')
            value=json.loads(row[0])
            if not isinstance(value,dict):raise ValueError('report record is not an object')
            yield value


def json_array_records(path):
    decoder=json.JSONDecoder()
    with Path(path).open(encoding='utf-8') as handle:
        buffer='';eof=False
        def more():
            nonlocal buffer,eof
            chunk=handle.read(65536)
            if not chunk:eof=True
            buffer+=chunk
        def trim():
            nonlocal buffer
            buffer=buffer.lstrip()
            while not buffer and not eof:more();buffer=buffer.lstrip()
        trim()
        if not buffer.startswith('['):raise ValueError('legacy checkpoint is not a JSON array')
        buffer=buffer[1:];first=True
        while True:
            trim()
            if buffer.startswith(']'):
                buffer=buffer[1:];trim()
                if buffer or not eof:raise ValueError('legacy checkpoint trailing content')
                return
            if not first:
                if not buffer.startswith(','):raise ValueError('legacy checkpoint separator missing')
                buffer=buffer[1:];trim()
                if buffer.startswith(']'):raise ValueError('legacy checkpoint trailing comma')
            while True:
                try:value,end=decoder.raw_decode(buffer);break
                except json.JSONDecodeError:
                    if eof or len(buffer)>RECORD_LIMIT:raise ValueError('legacy checkpoint invalid or record too large')
                    more()
            if end>RECORD_LIMIT or not isinstance(value,dict):raise ValueError('legacy checkpoint invalid record')
            buffer=buffer[end:];first=False
            yield value


class FileRecords:
    def __init__(self,path,count=None,kind=None):
        self.path=Path(path);self.count=count
        self.kind=kind or ('csv' if self.path.suffix.lower()=='.csv' else 'json-array')
        if self.kind not in ('csv','json-array'):raise ValueError('record format invalid')
    def __iter__(self):
        return iter(csv_records(self.path) if self.kind=='csv' else json_array_records(self.path))
    def __len__(self):
        if self.count is None:self.count=sum(1 for _ in self)
        return self.count

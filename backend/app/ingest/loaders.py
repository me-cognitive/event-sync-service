"""Fetch raw source documents from local disk or S3.

Both loaders decode bytes explicitly as UTF-8 so the platform default
encoding (cp1252 on Windows) never leaks into the data.
"""

import json
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings


class SourceLoader(Protocol):
    def load(self, name: str) -> list[dict[str, Any]]: ...


def _decode(payload: bytes, name: str) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{name}: expected a JSON array of records")
    return data


class FileLoader:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def load(self, name: str) -> list[dict[str, Any]]:
        return _decode((self.data_dir / name).read_bytes(), name)


class S3Loader:
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3  # imported lazily so local runs don't need AWS config

        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client("s3")

    def load(self, name: str) -> list[dict[str, Any]]:
        key = f"{self.prefix.rstrip('/')}/{name}" if self.prefix else name
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return _decode(body, f"s3://{self.bucket}/{key}")


def build_loader(settings: Settings) -> SourceLoader:
    if settings.data_source == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET must be set when DATA_SOURCE=s3")
        return S3Loader(settings.s3_bucket, settings.s3_prefix)
    return FileLoader(settings.data_dir)

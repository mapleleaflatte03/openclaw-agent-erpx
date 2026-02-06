from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass

import boto3
from botocore.config import Config

from openclaw_agent.common.settings import Settings


@dataclass(frozen=True)
class S3ObjectRef:
    bucket: str
    key: str

    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_s3_uri(uri: str) -> S3ObjectRef:
    if not uri.startswith("s3://"):
        raise ValueError(f"unsupported uri: {uri}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"invalid s3 uri: {uri}")
    return S3ObjectRef(bucket=bucket, key=key)


def make_s3_client(settings: Settings):
    endpoint = settings.minio_endpoint
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        endpoint_url = endpoint
    else:
        scheme = "https" if settings.minio_secure else "http"
        endpoint_url = f"{scheme}://{endpoint}"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
        config=Config(s3={"addressing_style": "path"}),
    )


def ensure_buckets(settings: Settings) -> None:
    s3 = make_s3_client(settings)
    for bucket in [
        settings.minio_bucket_attachments,
        settings.minio_bucket_exports,
        settings.minio_bucket_evidence,
        settings.minio_bucket_kb,
        settings.minio_bucket_drop,
    ]:
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            s3.create_bucket(Bucket=bucket)


def upload_file(settings: Settings, bucket: str, key: str, path: str, content_type: str | None = None) -> S3ObjectRef:
    s3 = make_s3_client(settings)
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    s3.upload_file(path, bucket, key, ExtraArgs=extra or None)
    return S3ObjectRef(bucket=bucket, key=key)


def download_file(settings: Settings, ref: S3ObjectRef, dest_path: str) -> str:
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    s3 = make_s3_client(settings)
    s3.download_file(ref.bucket, ref.key, dest_path)
    return dest_path


def list_objects(settings: Settings, bucket: str, prefix: str) -> Iterable[S3ObjectRef]:
    s3 = make_s3_client(settings)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield S3ObjectRef(bucket=bucket, key=obj["Key"])

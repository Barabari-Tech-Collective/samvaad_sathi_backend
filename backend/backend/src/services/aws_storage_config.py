"""Account-portable storage configuration without an implicit legacy bucket."""

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from decouple import config


@dataclass(frozen=True, repr=False)
class StorageConfiguration:
    bucket: str
    region: str
    client_options: dict[str, str]
    public_base_url: str


def load_storage_configuration(read: Callable = config) -> StorageConfiguration:
    bucket = read("AWS_S3_BUCKET_NAME", default="").strip()
    if not bucket:
        raise ValueError("AWS_S3_BUCKET_NAME must explicitly select this environment's bucket")
    region = read("AWS_REGION", default="ap-south-1").strip()
    if not region:
        raise ValueError("AWS_REGION must not be blank")

    access = read("AWS_ACCESS_KEY_ID", default="").strip()
    secret = read("AWS_SECRET_ACCESS_KEY", default="").strip()
    token = read("AWS_SESSION_TOKEN", default="").strip()
    if bool(access) != bool(secret) or (token and not access):
        raise ValueError("Explicit AWS credentials require a complete access/secret pair")
    options = {"region_name": region}
    if access:
        options.update(aws_access_key_id=access, aws_secret_access_key=secret)
        if token:
            options["aws_session_token"] = token
    # With no explicit keys, boto3 resolves a role, web identity or named profile
    # through its normal provider chain, including credential refresh.
    public_base = read("AWS_S3_PUBLIC_BASE_URL", default="").strip().rstrip("/")
    if public_base:
        parsed = urlsplit(public_base)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("AWS_S3_PUBLIC_BASE_URL must be an HTTPS origin/path without credentials or query")
    else:
        public_base = f"https://{bucket}.s3.{region}.amazonaws.com"
    return StorageConfiguration(bucket, region, options, public_base)

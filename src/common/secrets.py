"""SSM Parameter Store wrapper with a 5-minute in-process TTL cache.

Lambda execution environments persist between invocations for several
minutes, so caching within the process avoids re-paying the SSM call
on every request. 5 minutes is a compromise: short enough that a
rotated secret is picked up within a coffee break, long enough to
dramatically cut SSM reads for hot Lambdas.

Usage:
    from common.secrets import get_secret
    token = get_secret("apify_token")   # reads /jobs-aggregator/apify_token
"""
import os
import time
from typing import Optional

import boto3

# Prefix can be overridden for tests or to share between environments.
_PREFIX = os.environ.get("SSM_PREFIX", "/jobs-aggregator")
_TTL_SECONDS = 300

# name -> (expires_at_epoch, value)
_cache: dict[str, tuple[float, str]] = {}
_client = None


def _get_client:
    global _client
    if _client is None:
        _client = boto3.client("ssm")
    return _client


def get_secret(name: str) -> str:
    """Fetch /jobs-aggregator/<name>, decrypted. Caches for 5 minutes."""
    now = time.time
    cached = _cache.get(name)
    if cached and cached[0] > now:
        return cached[1]

    full_name = f"{_PREFIX}/{name}"
    resp = _get_client.get_parameter(Name=full_name, WithDecryption=True)
    value = resp["Parameter"]["Value"]
    _cache[name] = (now + _TTL_SECONDS, value)
    return value


def clear_cache -> None:
    """Test helper — drops the in-process cache."""
    _cache.clear
    global _client
    _client = None

"""api_authorizer — HTTP API request authorizer that closes the
direct-to-API hole.

The dashboard is gated at the CloudFront edge by HTTP basic auth
(BasicAuthFunction in template.yaml). But the HTTP API has a *public*
execute-api URL. Without an authorizer, anyone who learns that URL can
hit the API directly and bypass the edge auth entirely — read the job
DB, trigger a scrape, drive Haiku-backed scoring cost, etc.

Defense:
  - CloudFront injects a secret header `x-origin-verify` on every request
    it forwards to the api origin (OriginCustomHeaders in template.yaml).
    The value is the same `BasicAuthBase64` the edge function checks — one
    secret, one trust boundary, no new parameter to manage.
  - This function is the HTTP API's DEFAULT authorizer. It authorizes a
    request iff that header carries the shared secret.
  - A direct hit to the execute-api URL has no `x-origin-verify` header,
    so API Gateway returns 401 *before invoking this Lambda* (a missing
    identity source is rejected at the gateway when caching is enabled).
    This function therefore only runs when the header is present — its job
    is to reject a *wrong* value.

Payload format: API Gateway HTTP API authorizer, payload format 2.0 with
simple responses enabled (EnableSimpleResponses: true). The handler
returns {"isAuthorized": bool}.

Comma-join note: if a viewer ever also sends `x-origin-verify`, CloudFront
may forward both the viewer value and the injected value comma-joined into
one header. We therefore check membership across the split values rather
than strict equality — the injected secret is always one of them for a
through-CloudFront request, and never present at all on a direct hit. The
comparison is constant-time (hmac.compare_digest) to avoid leaking the
secret through response timing.

Failure mode is fail-CLOSED: if the secret env var is somehow unset, every
request is denied (the dashboard breaks loudly) rather than the API
falling open to the public internet.
"""
import hmac
import os

_HEADER_NAME = "x-origin-verify"   # HTTP API lowercases all header names


def handler(event, context):
    expected = os.environ.get("ORIGIN_VERIFY_SECRET", "")

    headers = event.get("headers") or {}
    raw = headers.get(_HEADER_NAME)
    if raw is None:
        # Fall back to the resolved identity source if the gateway provided
        # it that way (single configured header source -> one value, possibly
        # comma-joined like the header above).
        ident = event.get("identitySource") or 
        raw = ident[0] if ident else ""

    authorized = False
    if expected:
        for candidate in str(raw or "").split(","):
            if hmac.compare_digest(candidate.strip, expected):
                authorized = True
                break

    return {"isAuthorized": authorized}

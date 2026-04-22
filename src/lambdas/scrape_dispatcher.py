"""scrape_dispatcher — fans out scrape requests to scrape_worker.

Two invocation paths:

  1. EventBridge Scheduler (daily at 06:00 UTC):
        event = {"sources": ["remoteok", "himalayas", "hnhiring"]}
     -> invoke ScrapeWorkerFn once per source asynchronously.

  2. HTTP POST /api/scrape/{source} (manual trigger for debugging):
        path_params.source = "remoteok"
     -> invoke ScrapeWorkerFn for that source asynchronously, return 202.

Async invocation (`InvocationType=Event`) means the dispatcher returns
in milliseconds without waiting for scrape_run to complete. Each
worker logs to its own CloudWatch stream and writes its own ScrapeRuns
row, so observability isn't compromised.
"""
import json
import os

import boto3

from common.logging import log

# Worker function name is passed in by the SAM template (env var bound
# via !Ref ScrapeWorkerFn). Hardcoding would break because CloudFormation
# appends a random suffix to function names.
_WORKER_FN = os.environ.get("SCRAPE_WORKER_FN_NAME")
_lambda = None


def _client:
    """Lazy boto3 client (faster cold starts when only the manual path
    fires and we never reach this code)."""
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _invoke_worker(source: str) -> None:
    """Fire-and-forget invoke of ScrapeWorkerFn for one source."""
    if not _WORKER_FN:
        raise RuntimeError("SCRAPE_WORKER_FN_NAME env var not set")
    _client.invoke(
        FunctionName=_WORKER_FN,
        InvocationType="Event",         # async; do not wait for response
        Payload=json.dumps({"source": source}).encode("utf-8"),
    )


def _json(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    # ---- HTTP path (manual trigger) -------------------------------------
    if "routeKey" in event:
        source = (event.get("pathParameters") or {}).get("source")
        if not source:
            return _json(400, {"error": "missing path parameter `source`"})
        try:
            _invoke_worker(source)
        except Exception as exc:
            log.error("dispatcher_invoke_failed", source=source, error=str(exc))
            return _json(500, {"error": str(exc)})
        log.info("dispatcher_invoked", source=source, via="http")
        return _json(202, {"ok": True, "invoked": source})

    # ---- Scheduled fan-out path -----------------------------------------
    sources = event.get("sources") or 
    if not isinstance(sources, list):
        # kwarg name avoids collision with the logger's positional `event` param.
        log.error("dispatcher_bad_event", payload=event)
        return {"ok": False, "error": "event.sources must be a list"}

    invoked: list[str] = 
    failed: list[dict] = 
    for source in sources:
        try:
            _invoke_worker(source)
            invoked.append(source)
        except Exception as exc:
            # Per project rule: never let one source failure abort the others.
            log.error("dispatcher_invoke_failed", source=source, error=str(exc))
            failed.append({"source": source, "error": str(exc)})
    log.info("dispatcher_fanout_done", invoked=invoked, failed=failed)
    return {"ok": True, "invoked": invoked, "failed": failed}

"""
Break down coverage by row status. Rescore Lambda only processes
status="active" rows; inactive/archived rows are EXPECTED to lack
`passed_prefilter`. This script tells us the true coverage % for the
active population versus the misleading global count.
"""
import os
import concurrent.futures
import boto3

TABLE = os.environ.get("JOBS_TABLE", "jobs-aggregator-JobsTable-1EV6UZWFB7MVY")
ddb = boto3.client("dynamodb", region_name="us-east-1")


def _scan_segment(segment: int) -> dict[tuple[str, bool], int]:
    """Count rows bucketed by (status, has_passed_prefilter)."""
    buckets: dict[tuple[str, bool], int] = {}
    token = None
    while True:
        kwargs = {
            "TableName": TABLE,
            "ProjectionExpression": "#s, passed_prefilter",
            "ExpressionAttributeNames": {"#s": "status"},
            "Segment": segment,
            "TotalSegments": 8,
        }
        if token:
            kwargs["ExclusiveStartKey"] = token
        resp = ddb.scan(**kwargs)
        for item in resp.get("Items", ):
            status = item.get("status", {}).get("S", "<none>")
            has_pf = "passed_prefilter" in item
            key = (status, has_pf)
            buckets[key] = buckets.get(key, 0) + 1
        token = resp.get("LastEvaluatedKey")
        if not token:
            break
    return buckets


def main -> None:
    # 8-way parallel scan for speed.
    combined: dict[tuple[str, bool], int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for b in pool.map(_scan_segment, range(8)):
            for k, v in b.items:
                combined[k] = combined.get(k, 0) + v

    # Aggregate per status.
    statuses = sorted({k[0] for k in combined})
    total = 0
    print(f"{'status':<16} {'with_pf':>8} {'without':>8} {'total':>8}  coverage")
    print("-" * 60)
    for s in statuses:
        w = combined.get((s, True), 0)
        wo = combined.get((s, False), 0)
        t = w + wo
        total += t
        pct = (w / t * 100) if t else 0
        print(f"{s:<16} {w:>8} {wo:>8} {t:>8}  {pct:>5.1f}%")
    print("-" * 60)
    print(f"{'TOTAL':<16} {'':>8} {'':>8} {total:>8}")


if __name__ == "__main__":
    main

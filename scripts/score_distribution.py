"""Post-rescore scoring distribution + top-20 listing.

Scans the Jobs table and prints:
  1. Bucket distribution (gated / 1-34 / 35-49 / 50-64 / 65-77 T2 / 78+ T1)
  2. Top 20 roles by score, so the original author can eyeball whether the top of the
     feed now matches his real career targets (gaming/music/immersive/
     mission-nonprofit + VP+ seniority + tech-or-strategy framing).
"""
import boto3

REGION = "us-east-1"
TABLE  = "jobs-aggregator-JobsTable-1EV6UZWFB7MVY"

ddb   = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(TABLE)

items = 
resp  = table.scan(
    ProjectionExpression="job_id, title, company, score, gates_triggered, track"
)
items.extend(resp.get("Items", ))
while "LastEvaluatedKey" in resp:
    resp = table.scan(
        ProjectionExpression="job_id, title, company, score, gates_triggered, track",
        ExclusiveStartKey=resp["LastEvaluatedKey"],
    )
    items.extend(resp.get("Items", ))

# --- Bucket counts ---
buckets = {"gated_0": 0, "1-34": 0, "35-49": 0, "50-64": 0, "65-77_T2": 0, "78+_T1": 0}
for j in items:
    s = int(j.get("score") or 0)
    if s == 0:
        buckets["gated_0"] += 1
    elif s < 35:
        buckets["1-34"] += 1
    elif s < 50:
        buckets["35-49"] += 1
    elif s < 65:
        buckets["50-64"] += 1
    elif s < 78:
        buckets["65-77_T2"] += 1
    else:
        buckets["78+_T1"] += 1

print(f"Total jobs scanned: {len(items)}\n")
print("Score buckets:")
for k, v in buckets.items:
    print(f"  {k:<12} {v:>4}")

# --- Top 20 ---
top20 = sorted(items, key=lambda j: int(j.get("score") or 0), reverse=True)[:20]
print("\nTop 20 scores:")
print(f"  {'score':<6} {'title':<60} {'company':<25}")
print(f"  {'-'*5:<6} {'-'*59:<60} {'-'*24:<25}")
for j in top20:
    s   = int(j.get("score") or 0)
    t   = (j.get("title") or "")[:58]
    c   = (j.get("company") or "")[:23]
    print(f"  {s:<6} {t:<60} {c:<25}")

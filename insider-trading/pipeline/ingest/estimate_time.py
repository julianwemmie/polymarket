"""Estimate total scrape time by pulling a few batches and measuring throughput."""
import time
import pandas as pd
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from flatten_json import flatten
from datetime import datetime, timezone

QUERY_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"

def count_events_at_timestamp(ts):
    """Query how many events exist up to a given timestamp."""
    # Goldsky doesn't support count queries, so we'll estimate differently
    pass

def run_batches(n_batches=20, at_once=1000):
    """Run n batches and measure time + event density."""
    transport = RequestsHTTPTransport(url=QUERY_URL, verify=True, retries=3)
    client = Client(transport=transport)

    last_timestamp = 0
    batch_times = []
    batch_records = []
    timestamps = []

    for i in range(n_batches):
        q_string = f'''query MyQuery {{
            orderFilledEvents(orderBy: timestamp, orderDirection: asc
                              first: {at_once}
                              where: {{timestamp_gt: "{last_timestamp}"}}) {{
                id
                timestamp
            }}
        }}'''

        start = time.time()
        res = client.execute(gql(q_string))
        elapsed = time.time() - start

        events = res['orderFilledEvents']
        if not events:
            break

        batch_times.append(elapsed)
        batch_records.append(len(events))

        first_ts = int(events[0]['timestamp'])
        last_ts = int(events[-1]['timestamp'])
        last_timestamp = last_ts

        first_dt = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        timestamps.append((first_ts, last_ts, first_dt, last_dt))

        print(f"Batch {i+1}: {len(events)} events, {elapsed:.2f}s, "
              f"{first_dt.strftime('%Y-%m-%d')} to {last_dt.strftime('%Y-%m-%d')}")

    return batch_times, batch_records, timestamps

def estimate_from_recent(at_once=1000):
    """Sample recent data to get current event density."""
    transport = RequestsHTTPTransport(url=QUERY_URL, verify=True, retries=3)
    client = Client(transport=transport)

    # Get the most recent events by querying desc
    q_string = f'''query MyQuery {{
        orderFilledEvents(orderBy: timestamp, orderDirection: desc, first: {at_once}) {{
            id
            timestamp
        }}
    }}'''

    start = time.time()
    res = client.execute(gql(q_string))
    elapsed = time.time() - start

    events = res['orderFilledEvents']
    if not events:
        return None

    newest_ts = int(events[0]['timestamp'])
    oldest_ts = int(events[-1]['timestamp'])
    time_span = newest_ts - oldest_ts

    newest_dt = datetime.fromtimestamp(newest_ts, tz=timezone.utc)
    oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)

    events_per_sec = len(events) / max(time_span, 1)
    events_per_day = events_per_sec * 86400

    print(f"\nRecent data sample ({elapsed:.2f}s query):")
    print(f"  {len(events)} events spanning {oldest_dt.strftime('%Y-%m-%d %H:%M')} to {newest_dt.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Time span: {time_span} seconds ({time_span/60:.1f} minutes)")
    print(f"  Current rate: {events_per_sec:.1f} events/sec = {events_per_day:,.0f} events/day")

    return events_per_sec, events_per_day, newest_ts


if __name__ == "__main__":
    print("=" * 60)
    print("ESTIMATING FULL SCRAPE TIME")
    print("=" * 60)

    # 1. Measure batch speed from the beginning
    print("\n--- Sampling early data (from timestamp 0) ---")
    batch_times, batch_records, timestamps = run_batches(n_batches=15)

    avg_batch_time = sum(batch_times) / len(batch_times)
    total_events_sampled = sum(batch_records)

    if timestamps:
        first_event_ts = timestamps[0][0]
        last_sampled_ts = timestamps[-1][1]
        time_covered = last_sampled_ts - first_event_ts

        first_dt = timestamps[0][2]
        last_dt = timestamps[-1][3]

    print(f"\nEarly data stats:")
    print(f"  Avg batch time: {avg_batch_time:.2f}s")
    print(f"  Events sampled: {total_events_sampled:,}")
    print(f"  Time covered: {first_dt.strftime('%Y-%m-%d')} to {last_dt.strftime('%Y-%m-%d')} ({time_covered/86400:.1f} days)")
    print(f"  Early density: {total_events_sampled / max(time_covered/86400, 1):,.0f} events/day")

    # 2. Sample recent data for current density
    print("\n--- Sampling recent data ---")
    result = estimate_from_recent()

    if result:
        current_rate, current_daily, newest_ts = result

        # 3. Estimate total events
        # Use a simple model: linear growth from early to current rate
        now = int(time.time())
        total_time_span = newest_ts - first_event_ts  # seconds from first event to now
        total_days = total_time_span / 86400

        early_daily = total_events_sampled / max(time_covered/86400, 1)

        # Rough estimate: average of early and current rate * total days
        # Reality is probably exponential, but this gives a ballpark
        avg_daily = (early_daily + current_daily) / 2
        estimated_total_events = avg_daily * total_days

        # More conservative: assume exponential growth
        # If early was X/day and current is Y/day over N days,
        # total ~ integral of X * (Y/X)^(t/N) dt from 0 to N
        import math
        if early_daily > 0 and current_daily > early_daily:
            growth_ratio = current_daily / early_daily
            k = math.log(growth_ratio) / total_days
            # integral of early_daily * e^(k*t) from 0 to total_days
            estimated_total_exponential = early_daily * (math.exp(k * total_days) - 1) / k
        else:
            estimated_total_exponential = estimated_total_events

        total_batches_linear = estimated_total_events / 1000
        total_batches_exp = estimated_total_exponential / 1000

        time_linear = total_batches_linear * avg_batch_time
        time_exp = total_batches_exp * avg_batch_time

        print(f"\n{'=' * 60}")
        print(f"ESTIMATES")
        print(f"{'=' * 60}")
        print(f"  Total time span: {total_days:.0f} days ({total_days/365:.1f} years)")
        print(f"  Early event rate: {early_daily:,.0f} events/day")
        print(f"  Current event rate: {current_daily:,.0f} events/day")
        print(f"  Avg batch time: {avg_batch_time:.2f}s per 1,000 events")
        print(f"")
        print(f"  Linear model (conservative lower bound):")
        print(f"    Est. total events: {estimated_total_events:,.0f}")
        print(f"    Est. batches: {total_batches_linear:,.0f}")
        print(f"    Est. time: {time_linear/3600:.1f} hours ({time_linear/86400:.1f} days)")
        print(f"")
        print(f"  Exponential model (likely more accurate):")
        print(f"    Est. total events: {estimated_total_exponential:,.0f}")
        print(f"    Est. batches: {total_batches_exp:,.0f}")
        print(f"    Est. time: {time_exp/3600:.1f} hours ({time_exp/86400:.1f} days)")
        print(f"{'=' * 60}")

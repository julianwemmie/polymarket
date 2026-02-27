import os
import json
import pandas as pd
from pathlib import Path
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
from flatten_json import flatten
from datetime import datetime, timezone
import subprocess
import time
from update_utils.update_markets import update_markets

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(PROJECT_ROOT / "data" / "ingest")))
GOLDSKY_DIR = DATA_DIR / "goldsky"

# Global runtime timestamp
RUNTIME_TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')

# Columns to save
COLUMNS_TO_SAVE = ['timestamp', 'maker', 'makerAssetId', 'makerAmountFilled', 'taker', 'takerAssetId', 'takerAmountFilled', 'transactionHash']

GOLDSKY_DIR.mkdir(parents=True, exist_ok=True)

CURSOR_FILE = str(GOLDSKY_DIR / 'cursor_state.json')


def save_cursor(timestamp, last_id, sticky_timestamp=None):
    """Save cursor state to file for efficient resume."""
    state = {
        'last_timestamp': timestamp,
        'last_id': last_id,
        'sticky_timestamp': sticky_timestamp
    }
    with open(CURSOR_FILE, 'w') as f:
        json.dump(state, f)


def get_latest_cursor():
    """Get the latest cursor state for efficient resume.
    Returns (timestamp, last_id, sticky_timestamp) tuple."""
    if os.path.isfile(CURSOR_FILE):
        try:
            with open(CURSOR_FILE, 'r') as f:
                state = json.load(f)
            timestamp = state.get('last_timestamp', 0)
            last_id = state.get('last_id')
            sticky_timestamp = state.get('sticky_timestamp')

            if sticky_timestamp is not None and last_id is None:
                print(f"Warning: Invalid cursor state (sticky_timestamp={sticky_timestamp} but last_id=None), clearing sticky state")
                sticky_timestamp = None

            if timestamp > 0:
                readable_time = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                print(f'Resuming from cursor state: timestamp {timestamp} ({readable_time}), id: {last_id}, sticky: {sticky_timestamp}')
                return timestamp, last_id, sticky_timestamp
        except Exception as e:
            print(f"Error reading cursor file: {e}")

    cache_file = str(GOLDSKY_DIR / 'orderFilled.csv')

    if not os.path.isfile(cache_file):
        print("No existing file found, starting from beginning of time (timestamp 0)")
        return 0, None, None

    try:
        result = subprocess.run(['tail', '-n', '1', cache_file], capture_output=True, text=True, check=True)
        last_line = result.stdout.strip()
        if last_line:
            header_result = subprocess.run(['head', '-n', '1', cache_file], capture_output=True, text=True, check=True)
            headers = header_result.stdout.strip().split(',')

            if 'timestamp' in headers:
                timestamp_index = headers.index('timestamp')
                values = last_line.split(',')
                if len(values) > timestamp_index:
                    last_timestamp = int(values[timestamp_index])
                    readable_time = datetime.fromtimestamp(last_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                    print(f'Resuming from CSV (no cursor file): timestamp {last_timestamp} ({readable_time})')
                    return last_timestamp - 1, None, None
    except Exception as e:
        print(f"Error reading latest file with tail: {e}")
        try:
            df = pd.read_csv(cache_file)
            if len(df) > 0 and 'timestamp' in df.columns:
                last_timestamp = df.iloc[-1]['timestamp']
                readable_time = datetime.fromtimestamp(int(last_timestamp), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                print(f'Resuming from CSV (no cursor file): timestamp {last_timestamp} ({readable_time})')
                return int(last_timestamp) - 1, None, None
        except Exception as e2:
            print(f"Error reading with pandas: {e2}")

    print("Falling back to beginning of time (timestamp 0)")
    return 0, None, None


def scrape(at_once=1000, max_batches=None):
    """Scrape OrderFilled events from Goldsky subgraph.

    Args:
        at_once: Number of events per query batch
        max_batches: If set, stop after this many batches (for testing)
    """
    QUERY_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn"
    print(f"Query URL: {QUERY_URL}")
    print(f"Runtime timestamp: {RUNTIME_TIMESTAMP}")

    last_timestamp, last_id, sticky_timestamp = get_latest_cursor()
    count = 0
    total_records = 0

    print(f"\nStarting scrape for orderFilledEvents")
    if max_batches:
        print(f"TEST MODE: Will stop after {max_batches} batches")

    output_file = str(GOLDSKY_DIR / 'orderFilled.csv')
    print(f"Output file: {output_file}")
    print(f"Saving columns: {COLUMNS_TO_SAVE}")

    while True:
        if max_batches and count >= max_batches:
            print(f"\nReached max_batches limit ({max_batches}). Stopping.")
            break

        if sticky_timestamp is not None:
            where_clause = f'timestamp: "{sticky_timestamp}", id_gt: "{last_id}"'
        else:
            where_clause = f'timestamp_gt: "{last_timestamp}"'

        q_string = '''query MyQuery {
                        orderFilledEvents(orderBy: timestamp, orderDirection: asc
                                             first: ''' + str(at_once) + '''
                                             where: {''' + where_clause + '''}) {
                            fee
                            id
                            maker
                            makerAmountFilled
                            makerAssetId
                            orderHash
                            taker
                            takerAmountFilled
                            takerAssetId
                            timestamp
                            transactionHash
                        }
                    }
                '''

        query = gql(q_string)
        transport = RequestsHTTPTransport(url=QUERY_URL, verify=True, retries=3)
        client = Client(transport=transport)

        try:
            res = client.execute(query)
        except Exception as e:
            print(f"Query error: {e}")
            print("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        if not res['orderFilledEvents'] or len(res['orderFilledEvents']) == 0:
            if sticky_timestamp is not None:
                last_timestamp = sticky_timestamp
                sticky_timestamp = None
                last_id = None
                continue
            print(f"No more data for orderFilledEvents")
            break

        df = pd.DataFrame([flatten(x) for x in res['orderFilledEvents']]).reset_index(drop=True)

        df = df.sort_values(['timestamp', 'id'], ascending=True).reset_index(drop=True)

        batch_last_timestamp = int(df.iloc[-1]['timestamp'])
        batch_last_id = df.iloc[-1]['id']
        batch_first_timestamp = int(df.iloc[0]['timestamp'])

        readable_time = datetime.fromtimestamp(batch_last_timestamp, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        if len(df) >= at_once:
            if batch_first_timestamp == batch_last_timestamp:
                sticky_timestamp = batch_last_timestamp
                last_id = batch_last_id
                print(f"Batch {count + 1}: Timestamp {batch_last_timestamp} ({readable_time}), Records: {len(df)} [STICKY - continuing at same timestamp]")
            else:
                sticky_timestamp = batch_last_timestamp
                last_id = batch_last_id
                print(f"Batch {count + 1}: Timestamps {batch_first_timestamp}-{batch_last_timestamp} ({readable_time}), Records: {len(df)} [STICKY - ensuring complete timestamp]")
        else:
            if sticky_timestamp is not None:
                last_timestamp = sticky_timestamp
                sticky_timestamp = None
                last_id = None
                print(f"Batch {count + 1}: Timestamp {batch_last_timestamp} ({readable_time}), Records: {len(df)} [STICKY COMPLETE]")
            else:
                last_timestamp = batch_last_timestamp
                print(f"Batch {count + 1}: Last timestamp {batch_last_timestamp} ({readable_time}), Records: {len(df)}")

        count += 1
        total_records += len(df)

        df = df.drop_duplicates(subset=['id'])
        df_to_save = df[COLUMNS_TO_SAVE].copy()

        if os.path.isfile(output_file):
            df_to_save.to_csv(output_file, index=None, mode='a', header=None)
        else:
            df_to_save.to_csv(output_file, index=None)

        save_cursor(last_timestamp, last_id, sticky_timestamp)

        if len(df) < at_once and sticky_timestamp is None:
            break

    # Clear cursor file on successful completion (only if we didn't hit max_batches)
    if not max_batches and os.path.isfile(CURSOR_FILE):
        os.remove(CURSOR_FILE)

    print(f"Finished scraping orderFilledEvents")
    print(f"Total new records: {total_records}")
    print(f"Output file: {output_file}")


def update_goldsky(max_batches=None):
    """Run scraping for orderFilledEvents"""
    print(f"\n{'='*50}")
    print(f"Starting to scrape orderFilledEvents")
    print(f"Runtime: {RUNTIME_TIMESTAMP}")
    print(f"{'='*50}")
    try:
        scrape(max_batches=max_batches)
        print(f"Successfully completed orderFilledEvents")
    except Exception as e:
        print(f"Error scraping orderFilledEvents: {str(e)}")

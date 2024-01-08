import os
import azure.functions as func
import logging
import json
import requests
from requests import Session
from dotenv import load_dotenv
from LogzioShipper.backup_container import BackupContainer
from azure.storage.blob import ContainerClient
from threading import Thread
from queue import Queue
import backoff
from typing import List
import time
from queue import Empty

# Load environment variables
load_dotenv()

# Initialize Azure Blob Storage container client
container_client = ContainerClient.from_connection_string(
    # On Azure
    conn_str=os.getenv("AzureWebJobsStorage"),
    # On local
    # conn_str=os.getenv("AZURE_STORAGE_CONNECTION_STRING"),

    container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME")
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Logz.io configuration
# LOGZIO_URL = os.getenv("LOGZIO_LISTENER")
LOGZIO_URL = os.getenv("LogzioURL")
# LOGZIO_TOKEN = os.getenv("LOGZIO_TOKEN")
LOGZIO_TOKEN = os.getenv("LogzioToken")
HEADERS = {"Content-Type": "application/json"}
RETRY_WAIT_FIXED = 2  # seconds for retry delay
thread_count = int(os.getenv('THREAD_COUNT', 4))

# Queue for batched logs
batch_queue = Queue()

# Backup Container
backup_container = BackupContainer(logging, container_client)

# Connection Pool (Session)
session = Session()

# Constants for batching
BUFFER_SIZE = int(os.getenv('BUFFER_SIZE', 100))  # Batch size
INTERVAL_TIME = int(os.getenv('INTERVAL_TIME', 10000)) / 1000  # Interval time in seconds


def add_timestamp(log):
    if 'time' in log:
        log['@timestamp'] = log['time']
    return log


def delete_empty_fields_of_log(log):
    if isinstance(log, dict):
        return {k: v for k, v in log.items() if v is not None and v != ""}
    elif isinstance(log, list):
        return [delete_empty_fields_of_log(item) for item in log]
    else:
        return log


@backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=3)
def send_batch(batch_data):
    try:
        # Join batch data to a single string before sending
        batch_str = ''.join(batch_data)
        response = session.post(LOGZIO_URL, params={"token": LOGZIO_TOKEN, "type": "type_log"}, headers=HEADERS,
                                data=batch_str)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send batch: {e}")
        backup_container.write_event_to_blob(batch_data, e)


def batch_creator(azeventhub):
    local_log_batch = []
    last_batch_time = time.time()
    for event in azeventhub:
        logs = process_eventhub_message(event)
        for log in logs:
            formatted_log = json.dumps(log) + '\n'
            local_log_batch.append(formatted_log)

        # Check the batch size or time interval after processing all logs from the event
        current_time = time.time()
        if len(local_log_batch) >= BUFFER_SIZE or (current_time - last_batch_time) >= INTERVAL_TIME:
            print(f"Adding batch of size {len(local_log_batch)} to the sending queue.")
            batch_queue.put(list(local_log_batch))  # Put a copy of the batch
            local_log_batch.clear()  # Clear the local batch
            last_batch_time = current_time

    # Check and send any remaining logs in the batch after processing all events
    if local_log_batch:
        print(f"Adding batch of size {len(local_log_batch)} to the sending queue.")
        batch_queue.put(list(local_log_batch))  # Put a copy of the batch
        local_log_batch.clear()  # Clear the local batch



def batch_sender():
    while True:
        try:
            batch = batch_queue.get(timeout=0.1)
            if batch:
                send_batch(batch)
                batch_queue.task_done()
        except Empty:
            continue


def start_batch_senders(thread_count=4):
    for _ in range(thread_count):
        Thread(target=batch_sender, daemon=True).start()


def process_eventhub_message(event):
    try:
        message_body = event.get_body().decode('utf-8')
        logs = []
        for line in message_body.splitlines():
            log_entry = json.loads(line)
            # Check if this log entry contains nested logs under 'records'
            if 'records' in log_entry and isinstance(log_entry['records'], list):
                logs.extend(log_entry['records'])  # Add nested logs individually
            else:
                logs.append(log_entry)
        return logs
    except Exception as e:
        logging.error(f"Error processing EventHub message: {e}")
        return []


def main(azeventhub: List[func.EventHubEvent]):
    batch_creator_thread = Thread(target=batch_creator, args=(azeventhub,), daemon=True)
    batch_creator_thread.start()
    start_batch_senders(thread_count=thread_count)
    batch_creator_thread.join()
    logging.info('EventHub trigger processing complete!')

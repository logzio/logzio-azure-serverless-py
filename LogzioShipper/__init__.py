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

# Queue for logs
log_queue = Queue()

# Backup Container
backup_container = BackupContainer(logging, container_client)

# Connection Pool (Session)
session = Session()


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
def send_log(log):
    response = session.post(LOGZIO_URL, params={"token": LOGZIO_TOKEN, "type": "type_log"}, headers=HEADERS, json=log)
    response.raise_for_status()


def log_sender():
    while True:
        log = log_queue.get()
        try:
            send_log(log)
        except Exception as e:
            logging.error(f"Failed to send log to Logz.io after retries: {e}")
            backup_container.write_event_to_blob(log, e)
        finally:
            log_queue.task_done()


def start_log_senders(thread_count=4):
    for _ in range(thread_count):
        Thread(target=log_sender, daemon=True).start()


def process_eventhub_message(event):
    try:
        message_body = event.get_body().decode('utf-8')
        return [json.loads(line) for line in message_body.splitlines()]
    except Exception as e:
        logging.error(f"Error processing EventHub message: {e}")
        return []


def main(azeventhub: List[func.EventHubEvent]):
    for event in azeventhub:
        logs = process_eventhub_message(event)
        for log in logs:
            log_queue.put(log)
    logging.info('EventHub trigger processing complete')


# Start log sender threads
start_log_senders(thread_count=thread_count)  # Use the thread count from the environment variable

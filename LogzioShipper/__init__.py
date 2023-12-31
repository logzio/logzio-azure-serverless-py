import os
import azure.functions as func
import logging
import json
import asyncio
import aiohttp
from dotenv import load_dotenv
from LogzioShipper.backup_container import BackupContainer
from azure.storage.blob import ContainerClient
from typing import List

# Load environment variables
load_dotenv()

# Initialize Azure Blob Storage container client
container_client = ContainerClient.from_connection_string(
    # On Azure
    # conn_str=os.getenv("AzureWebJobsStorage"),
    # On local
    conn_str=os.getenv("AZURE_STORAGE_CONNECTION_STRING"),

    container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME")
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Retry configuration for transient errors
MAX_RETRIES = 3
RETRY_WAIT_FIXED = 2000  # milliseconds


# Helper functions for log processing
def add_timestamp(log):
    # Add timestamp to log if 'time' field exists
    if 'time' in log:
        log['@timestamp'] = log['time']
    return log


def delete_empty_fields_of_log(log):
    # Remove empty fields from log
    if isinstance(log, dict):
        return {k: v for k, v in log.items() if v is not None and v != ""}
    elif isinstance(log, list):
        return [delete_empty_fields_of_log(item) for item in log]
    else:
        return log


async def send_logs_to_logzio(logs):
    """Asynchronously sends a batch of logs to Logz.io with custom retry logic."""
    logzio_url = os.getenv("LOGZIO_LISTENER")
    # logzio_url = os.getenv("LogzioURL")
    token = os.getenv("LOGZIO_TOKEN")
    # token = os.getenv("LogzioToken")
    params = {"token": token, "type": "type_bar"}
    headers = {"Content-Type": "application/json"}

    failed_logs = []

    async with aiohttp.ClientSession() as session:
        for log in logs:
            attempts = 0
            while attempts < MAX_RETRIES:
                try:
                    async with session.post(logzio_url, params=params, headers=headers, json=log) as response:
                        if response.status == 200:
                            logging.info(f"Sent data to Logz.io: {response.status}, {await response.text()}")
                            break  # Exit the retry loop on success
                        else:
                            raise Exception(f"Logz.io response: {response.status}, {await response.text()}")
                except Exception as e:
                    logging.error(f"Attempt {attempts + 1} failed to send log to Logz.io: {e}")
                    if attempts == MAX_RETRIES - 1:
                        failed_logs.append(log)  # Add to failed logs after final attempt
                    attempts += 1
                    await asyncio.sleep(RETRY_WAIT_FIXED / 1000)

        return failed_logs


async def handle_log_transmission(logs, backup_container):
    failed_logs = await send_logs_to_logzio(logs)
    if failed_logs:
        for log in failed_logs:
            try:
                await backup_container.write_event_to_blob(log, Exception("Log failed to send"))
            except Exception as ex:
                logging.error(f"Failed to back up log: {ex}")
        await backup_container.upload_files()


async def process_eventhub_message(event, logs_to_send):
    """Process a single Event Hub message and accumulate logs."""
    try:
        message_body = event.get_body().decode('utf-8')
        for line in message_body.splitlines():
            log = json.loads(line)
            log = add_timestamp(log)
            log = delete_empty_fields_of_log(log)
            logs_to_send.append(log)  # Accumulate logs
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing JSON: {e}")
    except Exception as e:
        logging.error(f"Unexpected error processing event: {e}")


# Main function to process EventHub messages
async def main(azeventhub: List[func.EventHubEvent]):
    logging.info('Processing EventHub trigger')
    backup_container = BackupContainer(logging, container_client)

    logs_to_send = []
    for event in azeventhub:
        await process_eventhub_message(event, logs_to_send)

    if logs_to_send:
        await handle_log_transmission(logs_to_send, backup_container)

    logging.info('EventHub trigger processing complete')

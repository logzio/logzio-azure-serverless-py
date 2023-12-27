import os
import azure.functions as func
import logging
import json
import requests
from retrying import retry
from dotenv import load_dotenv
from backup_container import BackupContainer
from azure.storage.blob import ContainerClient

# Load environment variables
load_dotenv()

# Initialize Azure Blob Storage container client
container_client = ContainerClient.from_connection_string(
    conn_str=os.getenv("AzureWebJobsStorage"),
    # conn_str=os.getenv("AZURE_STORAGE_CONNECTION_STRING"),
    # container_name=os.getenv("AZURE_STORAGE_CONTAINER_NAME")
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


@retry(stop_max_attempt_number=MAX_RETRIES, wait_fixed=RETRY_WAIT_FIXED)
def send_log_to_logzio(log):
    # Send log to Logz.io
    # logzio_url = os.getenv("LOGZIO_LISTENER")
    logzio_url = os.getenv("LogzioLogsHost")
    # token = os.getenv("LOGZIO_TOKEN")
    token = os.getenv("LogzioToken")
    params = {"token": token, "type": "type_bar"}
    headers = {"Content-Type": "application/json"}
    response = requests.post(logzio_url, params=params, headers=headers, data=json.dumps(log))
    response.raise_for_status()
    logging.info(f"Sent data to Logz.io: {response.status_code}, {response.text}")


async def process_log(log, backup_container):
    # Process each log
    log = add_timestamp(log)
    log = delete_empty_fields_of_log(log)
    try:
        send_log_to_logzio(log)
    except Exception as e:
        logging.error(f"Failed to send log to Logz.io: {e}")
        await backup_container.write_event_to_blob(log, e)


app = func.FunctionApp()


# Main function
# @app.event_hub_message_trigger(arg_name="azeventhub", event_hub_name=os.getenv("EVENT_HUB_NAME"),
@app.event_hub_message_trigger(arg_name="azeventhub", event_hub_name=os.getenv("EventhubLogsName"),
                               connection="AzureWebJobsEventHubConnectionString")
# Main function to process EventHub messages
async def eventhub_trigger(azeventhub: func.EventHubEvent):
    logging.info('Processing EventHub trigger')
    backup_container = BackupContainer(logging, container_client)

    for message in azeventhub.get_body().decode('utf-8').splitlines():
        try:
            log = json.loads(message)
            await process_log(log, backup_container)
        except json.JSONDecodeError as e:
            logging.error(f"Error parsing JSON: {e}")

    await backup_container.upload_files()
    logging.info('EventHub trigger processing complete')

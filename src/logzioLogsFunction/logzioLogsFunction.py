from azure.storage.blob import ContainerClient
from logzio.handler import LogzioHandler
from backup_container import BackupContainer
import logging
import os
import json
import asyncio

# Configure your logger with Logz.io credentials
logger = logging.getLogger(__name__)
logzio_handler = LogzioHandler('your-logzio-token')
logger.addHandler(logzio_handler)

# Set the logging level if necessary (e.g., logging.INFO)
logger.setLevel(logging.INFO)


def create_python_logger(config):
    logger = logging.getLogger("logzio_shipper")
    logzio_token = config["token"]
    logzio_handler = LogzioHandler(logzio_token, host=config["host"])
    logger.addHandler(logzio_handler)
    logger.setLevel(logging.INFO)  # or another appropriate level
    return logger


def add_timestamp(log):
    if "time" in log:
        log["@timestamp"] = log["time"]
    return log


def is_iterable(obj):
    return obj is not None and hasattr(obj, "__iter__")


def is_object(obj):
    return obj is not None and isinstance(obj, dict)


def delete_empty_fields_of_log(obj):
    keys_to_delete = [key for key in obj if
                      key == "" or (isinstance(obj[key], dict) and delete_empty_fields_of_log(obj[key]))]
    for key in keys_to_delete:
        del obj[key]
    return obj


def get_callback_function(context):
    def callback(err):
        if err:
            context.log.error(f"logzio-logger error: {err}")
        context.log("Done.")

    # Python's equivalent of 'context.done()' goes here, if necessary
    return callback


def get_parser_options():
    return {
        "token": os.environ["LogzioLogsToken"],
        "host": os.environ["LogzioLogsHost"],
        "bufferSize": int(os.environ["BufferSize"]),
        "debug": os.environ["Debug"] == "true"
    }


def get_container_details():
    return {
        "storageConnectionString": os.environ["LogsStorageConnectionString"],
        "containerName": "logziologsbackupcontainer"
    }


def add_data_to_log(log, context):
    try:
        # Check if empty fields should be parsed, based on environment variable
        if os.environ.get("ParseEmptyFields", "").lower() == "true":
            log = delete_empty_fields_of_log(log)

        # Add a timestamp to the log
        log = add_timestamp(log)
    except Exception as error:
        context.log.error(error)

    return log


# Send log to Logz.io
async def send_log(log, logzio_shipper, backup_container, context):
    log = add_data_to_log(log, context)
    try:
        # Assuming logzio_shipper.log is an async function or has a Python equivalent
        await logzio_shipper.log(log)
    except Exception as error:
        await backup_container.write_event_to_blob(log, error)
        # Assuming backupContainer has equivalent methods in Python
        backup_container.update_folder_if_max_size_surpassed()
        backup_container.update_file_if_bulk_size_surpassed()
    finally:
        await backup_container.upload_files()
    return True


async def export_logs(event_hubs, logzio_shipper, backup_container, context):
    try:
        calls = []
        for message in event_hubs:
            events = message['records'] if 'records' in message else message
            if is_iterable(events):
                for log in events:
                    if 'records' in log and is_iterable(log['records']):
                        for inner_event in log['records']:
                            calls.append(send_log(inner_event, logzio_shipper, backup_container, context))
                    else:
                        calls.append(send_log(log, logzio_shipper, backup_container, context))
            else:
                calls.append(send_log(events, logzio_shipper, backup_container, context))

        await asyncio.gather(*calls)  # '*' unpacks the list calls
    except Exception as error:
        context.log(str(error))

    return True


async def process_event_hub_messages(context, event_hubs):
    callback_function = get_callback_function(context)
    parser_options = get_parser_options()
    debug = parser_options['debug']

    if debug:
        context.log(f"Messages: {json.dumps(event_hubs)}")

    # Setup for Logz.io shipper
    logzio_shipper = create_python_logger({
        "token": parser_options['token'],
        "host": parser_options['host']
        # Add additional configuration parameters if needed
    })

    container_details = get_container_details()
    container_client = ContainerClient(
        container_details['storageConnectionString'],
        container_details['containerName']
    )

    # Assuming BackupContainer is a Python class you have defined to handle backups
    backup_container = BackupContainer({
        "internalLogger": context,
        "containerClient": container_client,
    })

    try:
        await export_logs(event_hubs, logzio_shipper, backup_container, context)
        # Assuming sendAndClose is defined for your Python logzio_shipper
        await logzio_shipper.send_and_close(callback_function)
    except Exception as error:
        context.log.error(str(error))

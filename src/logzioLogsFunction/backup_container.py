import os
import json
import asyncio
from datetime import datetime
from azure.storage.blob import BlobClient, ContainerClient

class BackupContainer:
    def __init__(self, internal_logger, container_client):
        # Constructor: Initializes the backup container with logging and Azure container client.
        self._context = internal_logger
        self._container_client = container_client
        self.current_folder = None
        self.current_file = None
        self._files_to_upload = []
        self._folder_size = 0
        self._logs_in_bulk = 1
        self._create_new_folder()
        self._create_new_file()

    def _update_folder_size(self):
        # Updates the size of the current folder based on the size of the current file.
        file_path = os.path.join(self.current_folder, self.current_file)
        if os.path.exists(file_path):
            self._folder_size += os.path.getsize(file_path) / 1000  # Size in KB

    @staticmethod
    def _get_date():
        # Static method to get the current date in UTC.
        return datetime.utcnow().strftime("%Y-%m-%d")

    @staticmethod
    def _uniq_string():
        # Static method to generate a unique string for file naming.
        return os.urandom(16).hex()

    def _create_new_folder(self):
        # Creates a new folder for storing log files. Each folder represents a batch of logs.
        new_folder_name = os.path.join(os.tempdir, self._get_date() + "-" + self._uniq_string())
        os.makedirs(new_folder_name, exist_ok=True)
        self._folder_size = 0
        self.current_folder = new_folder_name

    def _create_new_file(self):
        # Creates a new file within the current folder for storing logs.
        self._logs_in_bulk = 1
        self.current_file = f"logs-{self._uniq_string()}.txt"

    def update_folder_if_max_size_surpassed(self, folder_max_size_in_mb=10000):
        # Checks if the current folder has reached its maximum size. If so, creates a new folder.
        if self._folder_size >= folder_max_size_in_mb:
            self._create_new_folder()

    def update_file_if_bulk_size_surpassed(self, max_shipper_bulk_size=100):
        # Checks if the current file has reached its maximum log count. If so, creates a new file.
        if self._logs_in_bulk >= max_shipper_bulk_size:
            self._update_folder_size()
            self._create_new_file()

    async def upload_files(self):
        # Asynchronously uploads files to Azure Blob Storage.
        try:
            for file in self._files_to_upload:
                blob_client = self._container_client.get_blob_client(file)
                with open(file, 'rb') as data:
                    await blob_client.upload_blob(data)
            self._context.log("Uploaded logs to back up container.")
            self._files_to_upload = []
        except Exception as error:
            self._context.log.error(error)

    async def write_event_to_blob(self, event, error):
        # Writes an event to a local file and queues it for upload. This is used when log sending fails.
        event_with_new_line = json.dumps(event) + "\n"
        file_full_path = os.path.join(self.current_folder, self.current_file)
        try:
            with open(file_full_path, 'a') as file:
                file.write(event_with_new_line)
            self._logs_in_bulk += 1
            if file_full_path not in self._files_to_upload:
                self._files_to_upload.append(file_full_path)
        except Exception as error:
            self._context.log.error(f"Error in appendFile: {error}")

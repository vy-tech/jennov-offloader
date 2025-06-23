#!/usr/bin/env python3

import argparse
import configparser
import json
import time
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import os

class JennovOffloader:
    def __init__(self):
        self.init_args()
        self.init_config()
        self.init_logging()
        self.init_camera_client()
    
    def init_args(self):
        parser = argparse.ArgumentParser(description='Jennov Camera Video Offloader')
        parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')
        parser.add_argument('-d', '--date', help='Date to process (YYYY-MM-DD)')
        parser.add_argument('-q', '--query-only', action='store_true', help='Only query recordings without downloading or deleting')
        parser.add_argument('--download-only', action='store_true', help='Download files without deleting')
        parser.add_argument('--delete-only', action='store_true', help='Delete files without downloading')
        parser.add_argument('-c', '--camera', help='Camera name to process (e.g., marysville1)')
        parser.add_argument('-a', '--all-cameras', action='store_true', help='Process all configured cameras')
        parser.add_argument('--list-cameras', action='store_true', help='List all configured cameras and exit')
        
        self.args = parser.parse_args()

    def init_config(self):
        self.config = configparser.ConfigParser()
        self.config.read("jennov-offloader.conf")

        self.secrets = configparser.ConfigParser()
        self.secrets.read("jennov-offloader-secrets.conf")

        # Parse camera configurations
        self.cameras = {}
        for section in self.config.sections():
            if section.startswith('camera:'):
                camera_name = section.split(':', 1)[1]
                self.cameras[camera_name] = {
                    'id': self.config.get(section, 'id'),
                    'ip_address': self.config.get(section, 'ip_address'),
                    'url': f"http://{self.config.get(section, 'ip_address')}"
                }

        if not self.cameras:
            raise ValueError("No cameras configured. Please add camera sections to jennov-offloader.conf")

    def init_logging(self):
        self.log = logging.getLogger(__name__)
        level = self.config.get('DEFAULT', 'log_level', fallback='INFO')

        # Override log level with -v
        if self.args.verbose:
            level = 'DEBUG'

        format = self.config.get('DEFAULT', 'log_format', fallback='[%(asctime)s %(levelname)s] %(message)s')

        logging.basicConfig(level=logging.getLevelName(level), format=format)

    def init_camera_client(self):
        """Initialize camera connection and authentication"""
        # Get authentication credentials from secrets (shared across all cameras)
        self.username = self.secrets.get('DEFAULT', 'username')
        self.password = self.secrets.get('DEFAULT', 'password')
        self.userid = self.secrets.get('DEFAULT', 'userid')
        self.passwd_hash = self.secrets.get('DEFAULT', 'passwd_hash')
        
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest'
        })

    def build_soap_envelope(self, body_content):
        """Build SOAP envelope with authentication headers"""
        return (f'<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2001/12/soap-envelope">'
                f'<soap:Header>\t<userid>{self.userid}</userid>\t<passwd>{self.passwd_hash}</passwd></soap:Header>'
                f'<soap:Body>{body_content}</soap:Body></soap:Envelope>')

    def build_soap_query(self, start_time, end_time, skip_count):
        """Build SOAP query for recording information"""
        body_content = (f'<RecordQueryInfo><Condition record_mode="-1" media_type="3" stream_index="-1" min_size="-1"'
                f' max_size="-1" start_time="{start_time}" end_time="{end_time}" skipCount="{skip_count}" />'
                f'</RecordQueryInfo>')
        
        return self.build_soap_envelope(body_content)

    def post_query(self, soap_envelope, camera_url):
        try:
            response = self.session.post(
                f"{camera_url}/getRecordQueryInfo",
                data=soap_envelope
            )
            response.raise_for_status()
            
            results = self.parse_query_response(response.text)

            return results
            
        except requests.RequestException as e:
            self.log.error(f"Failed to query recordings: {e}")
            return []
    
    def query_recordings(self, target_date, camera_url):
        """Query available recordings from camera"""
        
        start_time = target_date.strftime('%Y-%m-%d 00:00:00')
        end_time = target_date.strftime('%Y-%m-%d 23:59:59')
        skip_count = 0  # Start from the beginning
        max_skip_count = 24 * 60 # (Videos are a minimum of 1 minute each, so 24 hours * 60 minutes)
        recordings = []
        
        while skip_count <= max_skip_count:
            soap_envelope = self.build_soap_query(start_time, end_time, skip_count)
            
            self.log.debug(f"Querying recordings from {start_time} to {end_time} with skip count {skip_count}")

            response = self.post_query(soap_envelope, camera_url)
            
            if not response:
                self.log.info("No more recordings found.")
                break
            
            recordings.extend(response)
            skip_count += len(response)  # Increment skip count by number of results returned
            
        return recordings

    def parse_query_response(self, response_text):
        """Parse XML response to extract recording information"""
        recordings = []
        
        try:
            # Parse the XML response
            root = ET.fromstring(response_text)
            
            for item in root.findall('items'):
                recording = {
                    'filepath': item.get('filepath'),
                    'filesize': int(item.get('filesize')),
                    'record_mode': item.get('record_mode'),
                    'media_type': item.get('media_type'),
                    'stream_index': item.get('stream_index'),
                    'start_time': item.get('start_time')
                }
                # Parse start_time into a datetime object
                recording['start_time'] = datetime.strptime(recording['start_time'], '%Y-%m-%d %H:%M:%S')
                recordings.append(recording)
                
        except ET.ParseError as e:
            self.log.error(f"Failed to parse query response: {e}")
            
        self.log.info(f"Found {len(recordings)} recordings")
        return recordings

    def download_recording(self, recording, camera_url, camera_name):
        """Download a specific recording"""
        filepath = recording['filepath']
        filename = os.path.basename(filepath)
        camera_id = self.cameras[camera_name]['id']

        download_dir = self.config.get('DEFAULT', 'download_directory', fallback='./downloads')
        # Create camera-specific subdirectory
        camera_download_dir = os.path.join(download_dir, camera_id)
        os.makedirs(camera_download_dir, exist_ok=True)
        
        recording_date = recording['start_time'].strftime('%Y%m%d')
        recording_time = recording['start_time'].strftime('%H%M%S')
        local_filename = f"{camera_name}-{recording_date}-{recording_time}.mp4"
        local_path = os.path.join(camera_download_dir, local_filename)
        download_url = f"{camera_url}/playback{filepath}"
        
        try:
            self.log.info(f"Downloading {filename} from {camera_name} ({recording['filesize']} bytes)")
            
            response = self.session.get(download_url, stream=True)
            response.raise_for_status()
            
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            self.log.info(f"Downloaded {filename} to {local_path}")
            return True
            
        except requests.RequestException as e:
            self.log.error(f"Failed to download {filename}: {e}")
            return False

    def delete_recording(self, recording, camera_url, camera_name):
        """Delete a recording from camera SD card"""
        filepath = recording['filepath']
        filename = os.path.basename(filepath)
        
        # Build SOAP envelope with just the filepath in the body (matching HAR file)
        soap_envelope = self.build_soap_envelope(filepath)
        
        try:
            self.log.info(f"Deleting {filename} from {camera_name}")
            
            response = self.session.post(
                f"{camera_url}/setDeleteFile",
                data=soap_envelope
            )
            
            if response.status_code == 202:  # Accepted (as shown in HAR file)
                self.log.info(f"Successfully deleted {filename} from {camera_name}")
                return True
            else:
                self.log.error(f"Failed to delete {filename} from {camera_name}: HTTP {response.status_code}")
                return False
                
        except requests.RequestException as e:
            self.log.error(f"Failed to delete {filename} from {camera_name}: {e}")
            return False

    def list_cameras(self):
        """List all configured cameras"""
        print("Configured cameras:")
        for name, config in self.cameras.items():
            print(f"  {name}: {config['ip_address']} (ID: {config['id']})")

    def get_selected_cameras(self):
        """Get list of cameras to process based on command line arguments"""
        if self.args.list_cameras:
            self.list_cameras()
            return []
        
        if self.args.all_cameras:
            return list(self.cameras.keys())
        elif self.args.camera:
            if self.args.camera not in self.cameras:
                available = ', '.join(self.cameras.keys())
                raise ValueError(f"Camera '{self.args.camera}' not found. Available cameras: {available}")
            return [self.args.camera]
        else:
            # If no camera specified, use the first one or show error
            if len(self.cameras) == 1:
                camera_name = list(self.cameras.keys())[0]
                self.log.info(f"No camera specified, using: {camera_name}")
                return [camera_name]
            else:
                available = ', '.join(self.cameras.keys())
                raise ValueError(f"Multiple cameras available. Please specify one with -c or use -a for all. Available: {available}")

    def process_camera(self, camera_name, target_date):
        """Process recordings for a single camera"""
        camera_config = self.cameras[camera_name]
        camera_url = camera_config['url']
        
        self.log.info(f"Processing camera: {camera_name} ({camera_config['ip_address']})")
        
        # Query recordings
        recordings = self.query_recordings(target_date, camera_url)
        
        if not recordings:
            self.log.info(f"No recordings found for {camera_name}")
            return
            
        self.log.info(f"Found {len(recordings)} recordings for {camera_name}")
        
        # Process recordings
        for recording in recordings:
            filename = os.path.basename(recording['filepath'])
            
            if self.args.query_only:
                self.log.info(type(recording['start_time']))
                self.log.info(f"{camera_name}: {filename} {recording['start_time']} {recording['filesize']} bytes")
                continue

            # Download if not delete-only
            if not self.args.delete_only:
                if not self.download_recording(recording, camera_url, camera_name):
                    self.log.error(f"Skipping {filename} from {camera_name} due to download failure")
                    continue
                    
            # Delete if not download-only
            if not self.args.download_only:
                self.delete_recording(recording, camera_url, camera_name)

    def start(self):
        self.log.info("JennovOffloader starting...")
        
        # Get selected cameras
        selected_cameras = self.get_selected_cameras()
        
        if not selected_cameras:
            return  # Exit if --list-cameras was used
        
        # Determine date range
        if self.args.date:
            target_date = datetime.strptime(self.args.date, '%Y-%m-%d')
        else:
            target_date = datetime.now() - timedelta(days=1)  # Yesterday by default
            
        self.log.info(f"Processing recordings for {target_date.strftime('%Y-%m-%d')}")
        
        # Process each selected camera
        for camera_name in selected_cameras:
            try:
                self.process_camera(camera_name, target_date)
            except Exception as e:
                self.log.error(f"Failed to process camera {camera_name}: {e}")
                continue
                
        self.log.info("JennovOffloader completed.")

if __name__ == "__main__":
    app = JennovOffloader()
    app.start()

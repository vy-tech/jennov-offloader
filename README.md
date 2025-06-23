# Jennov Camera Video Offloader

A Python tool to query, download, and delete video files from a Jennov IP Camera (Model P87). These files are recorded locally on the camera to an SD card.

The cameras support ONVIF, but the Recording service is not available or is non-standard. This tool uses SOAP calls captured from the camera's web interface.

## Features

- Query recordings by date range
- Download video files from camera SD card
- Delete video files from camera SD card
- Configurable download directory
- Command-line options for download-only or delete-only operations
- Detailed logging with debug mode

## Setup

1. Copy configuration files:

   ```bash
   cp jennov-offloader.conf.sample jennov-offloader.conf
   cp jennov-offloader-secrets.conf.sample jennov-offloader-secrets.conf
   ```

2. Edit `jennov-offloader.conf` to set your camera's IP address and download directory

3. Edit `jennov-offloader-secrets.conf` to set your camera credentials:
   - Set `username` and `password` for your camera
   - Extract `userid` and `passwd_hash` from browser HAR files when accessing your camera

## Usage

```bash
# Process yesterday's recordings (download and delete)
python jennov-offloader.py

# Process specific date
python jennov-offloader.py -d 2025-06-22

# Download only (don't delete from camera)
python jennov-offloader.py --download-only

# Delete only (don't download)
python jennov-offloader.py --delete-only

# Enable verbose logging
python jennov-offloader.py -v
```

## Authentication Setup

To get the `userid` and `passwd_hash` values:

1. Open your camera's web interface in a browser
2. Open browser Developer Tools (F12)
3. Go to Network tab
4. Perform a recording query on the camera interface
5. Find the `getRecordQueryInfo` request in the Network tab
6. Look at the request body for the SOAP envelope
7. Extract the `userid` and `passwd` values from the SOAP header
8. Add these to your `jennov-offloader-secrets.conf` file

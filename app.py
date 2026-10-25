import os
import re
import csv
from io import StringIO
from flask import Flask, render_template, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from pydub import AudioSegment
import tempfile
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

# Global variables for playlist management
current_directory = None
current_playlist = [] # Stores full paths on server
audio_file_map = {}  # Maps filename to full path for nested directories
SUPPORTED_AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

# Global variable to store parsed log data
parsed_transcription_data = {}

# Global variables for CSV error labeling
csv_error_data = []
csv_file_loaded = False

def time_to_seconds(time_str):
    """Convert HH:MM:SS format to seconds"""
    try:
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:
                minutes, seconds = map(float, parts)
                return minutes * 60 + seconds
        return float(time_str)  # Try direct conversion as fallback
    except (ValueError, AttributeError):
        return 0.0

def parse_log_content(log_content):
    """
    Parses the log content to extract error segments for each audio file.
    Only supports the new CSV/TSV format as in sample_log.txt.
    Returns a dict mapping audio file basenames to a list of error segments.
    """
    data = {}
    # Auto-detect delimiter: use tab if more tabs than commas, else comma
    tab_count = log_content.count('\t')
    comma_count = log_content.count(',')
    delimiter = '\t' if tab_count > comma_count else ','
    reader = csv.reader(StringIO(log_content), delimiter=delimiter)
    header = next(reader, None)
    # Find relevant column indices
    def col(name):
        try:
            return header.index(name)
        except ValueError:
            return None
    col_audio = col('transcriptFile')
    col_long_start = col('longFormStart')
    col_long_end = col('longFormEnd')
    col_long_error = col('longFormError')
    col_short_error = col('shortFormError')
    col_short_start = col('shortFormStart')
    col_short_end = col('shortFormEnd')
    for row in reader:
        if not row or len(row) <= max(filter(None, [col_audio, col_long_start, col_long_end, col_long_error, col_short_error, col_short_start, col_short_end])):
            continue
        audio_path = row[col_audio]
        filename = os.path.basename(audio_path)
        try:
            long_start = float(row[col_long_start]) if col_long_start is not None else None
            long_end = float(row[col_long_end]) if col_long_end is not None else None
            short_start = float(row[col_short_start]) if col_short_start is not None and row[col_short_start] else None
            short_end = float(row[col_short_end]) if col_short_end is not None and row[col_short_end] else None
        except (ValueError, IndexError):
            long_start = long_end = short_start = short_end = None
        segment = {
            'longFormStart': long_start,
            'longFormEnd': long_end,
            'longFormError': row[col_long_error] if col_long_error is not None else '',
            'shortFormError': row[col_short_error] if col_short_error is not None else '',
            'shortFormStart': short_start,
            'shortFormEnd': short_end
        }
        if filename not in data:
            data[filename] = []
        data[filename].append(segment)
    return data

@app.route('/audio_files/<path:filename>')
def serve_audio_file(filename):
    """
    Serves audio files directly to the client's web browser.

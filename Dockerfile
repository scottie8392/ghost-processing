FROM python:3.13-slim

# Install SoX (resampling + dithering) and FFmpeg (pydub audio decoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sox \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY process_audio.py verify_audio.py app.py ./
COPY templates/ templates/
COPY docker-compose.yml ./

# Web UI — accessible at http://NAS_IP:5001
CMD ["python", "app.py"]

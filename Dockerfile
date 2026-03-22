FROM python:3.13-slim

# Install SoX (resampling + dithering) and FFmpeg (pydub audio decoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sox \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY process_audio.py verify_audio.py ./

# Config is mounted at runtime via docker-compose volume
CMD ["python", "process_audio.py", "--config", "/config/config.docker.yaml"]

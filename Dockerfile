FROM python:3.10-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl ffmpeg wget bash neofetch && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .

RUN pip3 install wheel
RUN pip3 install --no-cache-dir -U -r requirements.txt
WORKDIR /app
COPY . .

CMD ["python3", "app.py"]



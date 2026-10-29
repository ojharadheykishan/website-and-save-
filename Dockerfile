FROM python:3.10-slim-bookworm
RUN apt-get update \
	&& apt-get install -y --no-install-recommends git curl ffmpeg wget bash \
	&& rm -rf /var/lib/apt/lists/*
COPY requirements.txt .

RUN pip3 install wheel
RUN pip3 install --no-cache-dir -U -r requirements.txt
WORKDIR /app
COPY . .
RUN mkdir -p safe_repo/core safe_repo/web \
	&& ln -sfn ../../media_links.py safe_repo/core/media_links.py \
	&& ln -sfn ../../admin.py safe_repo/web/admin.py \
	&& ln -sfn ../../study.py safe_repo/web/study.py

CMD ["python3", "app.py"]



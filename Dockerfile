FROM python:3.10-slim-bookworm
RUN apt-get update \
	&& apt-get install -y --no-install-recommends git curl ffmpeg wget bash \
	&& rm -rf /var/lib/apt/lists/*
COPY requirements.txt .

RUN pip3 install wheel
RUN pip3 install --no-cache-dir -U -r requirements.txt
WORKDIR /app
COPY . .
RUN mkdir -p safe_repo/core/mongo safe_repo/web safe_repo/modules \
	&& ln -sfn ../__init__.py safe_repo/__init__.py \
	&& ln -sfn ../__main__.py safe_repo/__main__.py \
	&& ln -sfn ../../script.py safe_repo/core/script.py \
	&& ln -sfn ../../func.py safe_repo/core/func.py \
	&& ln -sfn ../../get_func.py safe_repo/core/get_func.py \
	&& ln -sfn ../../media_links.py safe_repo/core/media_links.py \
	&& ln -sfn ../../../db.py safe_repo/core/mongo/db.py \
	&& ln -sfn ../../../plans_db.py safe_repo/core/mongo/plans_db.py \
	&& ln -sfn ../../../users_db.py safe_repo/core/mongo/users_db.py \
	&& ln -sfn ../../admin.py safe_repo/web/admin.py \
	&& ln -sfn ../../study.py safe_repo/web/study.py \
	&& ln -sfn '../../__init__ (3).py' safe_repo/modules/__init__.py \
	&& for module in gcast login main plans start stats stream; do \
		ln -sfn "../../${module}.py" "safe_repo/modules/${module}.py"; \
	done

CMD ["python3", "app.py"]



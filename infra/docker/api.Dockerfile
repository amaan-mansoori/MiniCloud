FROM python:3.12-slim

WORKDIR /workspace
COPY services/api/requirements.txt /workspace/services/api/requirements.txt
RUN pip install --no-cache-dir -r /workspace/services/api/requirements.txt
RUN useradd --system --uid 10001 --create-home --home-dir /home/minicloud minicloud
COPY . /workspace

ENV PYTHONPATH=/workspace/services/api
ENV PYTHONDONTWRITEBYTECODE=1
USER minicloud

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

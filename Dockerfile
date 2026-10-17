FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 openjdk-17-jdk-headless && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-wallet.txt ./
RUN pip install --no-cache-dir -r requirements-wallet.txt pyspark==4.0.3
ENV SPARK_HOME=/usr/local/lib/python3.12/site-packages/pyspark
COPY . .
RUN scripts/build_spark.sh
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

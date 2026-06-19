FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY webapp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY webapp/ ./webapp/
COPY crawl_budget_analyzer/ ./crawl_budget_analyzer/
COPY example_priority.yaml ./

# Create a writable directory for the SQLite audit log
RUN mkdir -p /data && chmod 777 /data

ENV AUDIT_LOG_PATH=/data/audit_log.db

EXPOSE 8080

CMD ["streamlit", "run", "webapp/app.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]

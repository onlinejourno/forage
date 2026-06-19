FROM python:3.11-slim

WORKDIR /app

COPY webapp/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Audit log lives on a persistent Fly volume mounted at /data
ENV AUDIT_LOG_PATH=/data/audit_log.db

EXPOSE 8501

CMD ["streamlit", "run", "webapp/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]

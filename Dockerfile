# Forage — FastAPI JSON API (OnlineJourno Tools).
# Canonical image for this tool. Build and run it wherever you host it.
FROM python:3.11-slim

WORKDIR /app

COPY webapp/requirements-api.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY webapp/ ./webapp/

EXPOSE 8080

CMD ["uvicorn", "webapp.api:app", "--host", "0.0.0.0", "--port", "8080"]

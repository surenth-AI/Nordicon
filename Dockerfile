FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium (smallest footprint)
RUN playwright install chromium --with-deps

COPY session_keeper.py .

CMD ["python", "session_keeper.py"]

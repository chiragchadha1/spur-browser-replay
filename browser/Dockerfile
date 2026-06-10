# Headless Chromium via Playwright. Native arch (no amd64 pin), no Xvfb.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Chromium and its OS dependencies
RUN playwright install --with-deps chromium

ENV PYTHONUNBUFFERED=1
COPY launch_browser.py .

EXPOSE 9222
CMD ["python", "launch_browser.py"]

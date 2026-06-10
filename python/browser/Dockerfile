# Browser + driver service: Playwright-controlled headless Chrome that injects
# rrweb and runs the scripted interaction driver.
#
# We intentionally do NOT pin --platform=linux/amd64 (the starter did), so on
# Apple Silicon this builds a native arm64 image and Chromium runs without qemu
# emulation. Headless Chrome needs no Xvfb, so the X stack from the starter is gone.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Installs Chromium and the OS libraries it needs, matched to this Playwright version.
RUN playwright install --with-deps chromium

# Set late so the expensive layers above stay cached on rebuilds.
ENV PYTHONUNBUFFERED=1
COPY launch_browser.py .

# CDP endpoint (Playwright drives Chrome over this); exposed for inspection.
EXPOSE 9222
CMD ["python", "launch_browser.py"]

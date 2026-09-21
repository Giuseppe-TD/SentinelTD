# Immagine ufficiale Playwright: Chromium + tutte le librerie di sistema gia' dentro.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Google Chrome: il Chromium incluso in Playwright NON ha i codec proprietari
# (H.264/AAC), quindi i video di sfondo in MP4 non vengono decodificati e nello
# screenshot resta un rettangolo vuoto. Chrome li supporta. Se il download fallisce
# il build prosegue: lo shooter ripiega su Chromium da solo.
RUN playwright install --with-deps chrome || echo "Chrome non installato: verra' usato Chromium"

COPY shooter.py .

EXPOSE 8090
CMD ["uvicorn", "shooter:app", "--host", "0.0.0.0", "--port", "8090"]

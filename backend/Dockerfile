FROM python:3.12-slim

WORKDIR /app

# ffmpeg — только ради кадра-обложки к видео: без неё в переписке вместо
# ролика висит серый прямоугольник, и понять, что прислали, можно лишь скачав.
# Ставим headless-сборку: кодировать и показывать нам нечего, нужен один кадр.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads /app/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

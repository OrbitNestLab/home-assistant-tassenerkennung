FROM python:3.12.15-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2
RUN pip install --no-cache-dir onnxruntime==1.31.0 numpy==2.3.5 pillow==12.3.0 opencv-python-headless==4.13.0.92
WORKDIR /app
COPY visionlib /app/visionlib
COPY modelle /app/modelle
COPY server.py /app/server.py
COPY web.py /app/web.py
CMD ["python", "/app/server.py"]

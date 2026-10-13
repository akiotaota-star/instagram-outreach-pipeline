FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OUTREACH_DATA_DIR=/data TMPDIR=/data/tmp
WORKDIR /app
COPY outreach/ ./outreach/
COPY config/ ./config/
RUN useradd --create-home --uid 10001 outreach && mkdir -p /data/tmp && chown -R outreach:outreach /data
USER outreach
EXPOSE 8787
CMD ["python", "-B", "-m", "outreach", "serve", "--host", "0.0.0.0", "--port", "8787"]

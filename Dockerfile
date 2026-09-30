FROM python:3.12-slim
WORKDIR /app
# O .dockerignore deixa de fora data/, .env, testes e ferramentas (P14).
COPY . /app
ENV PYTHONUNBUFFERED=1
EXPOSE 5232
# P14: o Docker marca o contentor como "unhealthy" se a app deixar de responder.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '5232'), timeout=4)" || exit 1
CMD ["python","-m","backend.main"]

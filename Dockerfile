FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY config/business.example.yaml ./config/business.example.yaml

RUN pip install --no-cache-dir .

EXPOSE 8066

CMD ["sam-radar", "serve"]

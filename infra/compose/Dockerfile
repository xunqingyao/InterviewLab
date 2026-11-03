FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app/packages:/app/apps
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps ./apps
COPY seed ./seed
COPY evals ./evals
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

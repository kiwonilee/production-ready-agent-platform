FROM python:3.12-slim
WORKDIR /code
COPY . .
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]

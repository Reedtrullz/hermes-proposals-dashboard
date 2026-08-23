FROM python:3.12-slim

WORKDIR /app
ENV HERMES_HOME=/data/hermes
ENV HERMES_REQUIRE_AUTH=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY project_git_state.py .
COPY templates/ templates/

EXPOSE 8089
VOLUME ["/data/hermes"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8089"]

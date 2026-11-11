FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DP_DB=/tmp/dispatchproof.db PORT=7860
EXPOSE 7860
CMD ["python", "main.py"]

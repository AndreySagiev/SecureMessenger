FROM python:3.12-slim

WORKDIR /mnt/locust

RUN pip install --no-cache-dir locust==2.31.1

COPY locustfile.py .

EXPOSE 8089

CMD ["locust", "-f", "locustfile.py", "--host", "http://nginx", "--web-host", "0.0.0.0", "--web-port", "8089"]

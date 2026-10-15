# Supply an independently verified immutable Python base; never defaults to a tag.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY app.py /app.py
USER 65532:65532
EXPOSE 8080
CMD ["python3", "/app.py"]

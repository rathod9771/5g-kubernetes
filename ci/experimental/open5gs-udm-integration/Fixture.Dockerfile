# Local tag is built in this workflow from the unchanged hash-pinned native
# Builder.Dockerfile; never resolved from a registry under this name.
FROM l3-udm-stage8-toolchain
COPY fixture-dependencies.json wheels.py sbi_fixture.py /fixture/
COPY inputs/ /fixture/inputs/
RUN python3 /fixture/wheels.py /fixture /vendor
ENV PYTHONPATH=/vendor
ENTRYPOINT ["/usr/bin/python3", "-B", "/fixture/sbi_fixture.py"]

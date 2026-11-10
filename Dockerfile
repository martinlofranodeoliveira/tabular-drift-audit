ARG PREPARED_IMAGE=tabular-drift-prepared:local
FROM ${PREPARED_IMAGE} AS prepared

FROM python@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS sdk
COPY --from=prepared /opt/prepared /opt/prepared
RUN mkdir -p /work/project /work/home /work/tmp /work/results && chown -R 1000:1000 /work
COPY --chown=1000:1000 . /work/project
WORKDIR /work
USER 1000:1000
ENV HOME=/work/home TMPDIR=/work/tmp LANG=C.UTF-8 PATH=/usr/local/bin:/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 ARROW_NUM_THREADS=1
CMD ["python", "/work/project/scripts/offline.py"]

FROM python@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS runtime-assets
COPY --from=prepared /opt/prepared/wheelhouse /opt/wheelhouse
COPY locks/runtime.lock /opt/runtime.lock
COPY build/results/dist/*.whl /opt/application-wheel/
RUN python -m venv /opt/runtime && \
    /opt/runtime/bin/python -m pip install --no-index --require-hashes --find-links=/opt/wheelhouse -r /opt/runtime.lock && \
    /opt/runtime/bin/python -m pip install --no-index --no-deps /opt/application-wheel/*.whl

FROM python@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS runtime
COPY --from=runtime-assets /opt/runtime /opt/runtime
COPY --from=prepared /opt/prepared/notices /opt/notices
RUN mkdir -p /work/home /work/tmp && chown -R 1000:1000 /work
COPY --chown=1000:1000 scripts/installed_consumer.py /work/installed_consumer.py
WORKDIR /work
USER 1000:1000
ENV HOME=/work/home TMPDIR=/work/tmp LANG=C.UTF-8 PATH=/opt/runtime/bin:/usr/local/bin:/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 ARROW_NUM_THREADS=1
ENTRYPOINT ["/opt/runtime/bin/tabular-drift-audit"]
CMD ["demo", "--assert"]

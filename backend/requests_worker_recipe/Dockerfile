FROM python@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
LABEL codepilot.review_worker="true" codepilot.dependency_lock="5fd3ed128ed3bd26c4e0a9aa166f96f218ba7c977579823543472ed4474a4fd4"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS="" PYTEST_PLUGINS=""
COPY requirements.lock /opt/requirements.lock
COPY wheels /opt/wheels
RUN python -I -m venv /opt/worker-venv && /opt/worker-venv/bin/python -I -m pip install --isolated --no-index --no-cache-dir --only-binary=:all: --require-hashes --no-deps --find-links=/opt/wheels -r /opt/requirements.lock && /opt/worker-venv/bin/python -I -m pip check
ENV PATH="/opt/worker-venv/bin:$PATH"
USER 65534:65534
WORKDIR /tmp

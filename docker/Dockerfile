FROM python:3.12-slim

WORKDIR /app

# Install sentri
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[llm]"

# Initialize sentri (creates ~/.sentri with policies, DB, alerts, checks)
RUN sentri init

# Copy demo-specific config (overrides defaults)
COPY docker/demo-config.yaml /root/.sentri/config/sentri.yaml

ENTRYPOINT ["sentri"]
CMD ["start"]

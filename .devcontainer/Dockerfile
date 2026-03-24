FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    make \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

# Set workdir
WORKDIR /workspace

# Copy only dependency files first for better layer caching
COPY pyproject.toml ./

# Install all dependency groups
RUN pip install --upgrade pip \
    && pip install -e ".[dev,ingestion,features,training,serving,monitoring]"

USER $USERNAME

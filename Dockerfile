# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Install system dependencies required for building
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Rust and Cargo via rustup
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y

# Add Cargo to the PATH so pip can find it
ENV PATH="/root/.cargo/bin:${PATH}"

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Ensure the project root directory is included in the Python path
ENV PYTHONPATH=/app:/app/services:/app/models:$PYTHONPATH

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Create the data directory for the SQLite database
RUN mkdir -p /app/data

# Set environment variables for the Flask app
ENV PORT=5001
ENV FLASK_APP=run.py
ENV FLASK_ENV=production

# Make port available to the container
EXPOSE ${PORT}

# Run the production WSGI application with Gunicorn
CMD ["sh", "-c", "exec gunicorn --workers 4 --bind 0.0.0.0:${PORT:-5001} 'run:app'"]

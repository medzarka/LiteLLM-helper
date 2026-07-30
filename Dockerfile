# Use an official Python runtime as a parent image
FROM python:3.9-slim

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

# Make port 5001 available to the world outside this container
EXPOSE 5001

# Set the environment variable for the Flask app
ENV FLASK_APP=run.py
ENV FLASK_ENV=production

# Run the Flask application
CMD ["flask", "run", "--host=0.0.0.0", "--port=5001"]

# Use an official Python runtime as a parent image
# FROM python:3.12-slim
FROM python:alpine

# Set the working directory to /app
WORKDIR /app

# Copy the current directory contents into the container at /app
ADD . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir flask gunicorn

# Make port 5000 available to the world outside this container
EXPOSE 5000/tcp

# Define environment variable to specify the database path
ENV DATABASE /data/counters.db

# Run app.py when the container launches
CMD ["gunicorn", "-w 4", "-b", "0.0.0.0:5000", "app:app"]

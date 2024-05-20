# Use the official Python image
FROM python:alpine

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Install Redis and Supervisor
RUN apk add --no-cache redis supervisor

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisord.conf

# Make ports available to the world outside this container
EXPOSE 5000/tcp

# Copy the application
COPY . /app/

# Run Supervisor to manage both Redis and the application
CMD ["supervisord", "-c", "/etc/supervisord.conf"]

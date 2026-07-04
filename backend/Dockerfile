# Use an official lightweight Python image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required by MediaPipe and OpenCV
# libgles2 provides libGLESv2.so.2 which was causing the crash
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgles2 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend code
COPY . .

# Expose the port (Render sets the PORT environment variable)
EXPOSE 8000

# Start the FastAPI server using the PORT provided by Render, defaulting to 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

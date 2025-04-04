# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    # Set timezone if needed for scheduler (e.g., Asia/Jerusalem, America/New_York)
    TZ=Asia/Jerusalem \
    # Playwright browser download path within the container
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies needed for Playwright and its browsers
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Curl needed? Maybe not strictly, but can be useful for debugging
    # curl \
    # Playwright dependencies
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
    libgtk-3-0 libgbm1 libasound2 libxslt1.1 \
    # Clean up
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers - this can take some time
# Run as a non-root user for playwright install if needed, or ensure permissions
# Use --with-deps to try and install OS dependencies automatically (redundant with above RUN?)
RUN playwright install --with-deps chromium
# Or install specific browser: RUN playwright install chromium

# Copy the rest of the application code into the container
COPY . .

# Make port 8000 available to the world outside this container (Render uses this)
EXPOSE 8000

# Define environment variable for the port (Render injects PORT, Uvicorn uses --port)
# ENV PORT=8000 # Usually not needed as Render sets it

# Run main.py when the container launches using Uvicorn
# Use --host 0.0.0.0 to accept connections from outside the container
CMD ["uvicorn", "main:api", "--host", "0.0.0.0", "--port", "8000"]
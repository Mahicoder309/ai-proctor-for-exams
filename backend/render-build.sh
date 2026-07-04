#!/usr/bin/env bash
set -o errexit

echo "Installing python dependencies..."
pip install -r requirements.txt

echo "Fetching missing system libraries for MediaPipe (libGLESv2, etc)..."
mkdir -p local_libs
cd local_libs

# Download the debian packages for the missing OpenGL/GLES libraries
apt-get update -qq || true
apt-get download libgles2 || true
apt-get download libglvnd0 || true
apt-get download libgl1 || true
apt-get download libglib2.0-0 || true
apt-get download libegl1 || true

# Extract them into the local_libs folder
for f in *.deb; do
  if [ -f "$f" ]; then
    echo "Extracting $f..."
    dpkg -x "$f" .
  fi
done
cd ..

echo "Build complete!"

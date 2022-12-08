#!/bin/bash

if [[ -z $1 ]]
then
    echo -e "Please provide non-privileged user to run service on."
    exit 1
fi

if [[ `id -u` -ne 0 ]]
then
    echo -e "Please run this script as root."
    exit 1
fi

# Add user to video users to be able to use video cameras
echo -e "# Add user to video and dialout users to be able to use video cameras"
usermod -aG video "$1"
usermod -aG dialout "$1"

# Update packages and install requirements
echo -e "# Update packages and install requirements"
apt update
apt upgrade -y
apt install \
    git \
    python3-pip \
    pigpio \
    libcap-dev \
    libmagic1 \
    libturbojpeg0 \
    libatlas-base-dev \
    make \
    cmake \
    libjpeg-dev \
    python3-venv \
    python3-pip \
    -y

# Switch to specified user and perform final installation
echo "# Switch to specified user and perform final installation"
sudo -i -u "$1" bash << EOF
cd ~/
[[ -d PrusaLink ]] || mkdir PrusaLink
cd PrusaLink
python3 -m venv ./PrusaLink
. ./PrusaLink/bin/activate
pip3 install git+https://github.com/prusa3d/Prusa-Connect-SDK-Printer.git
pip3 install git+https://github.com/prusa3d/Prusa-Link.git
cd ..
EOF
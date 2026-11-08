#!/bin/bash
# Double-click to start the Controller Replay recorder.
cd "$(dirname "$0")"
python3 -c "import pygame" 2>/dev/null || python3 -m pip install --user pygame
python3 recorder.py

#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
cd gui
python main_window.py

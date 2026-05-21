@echo off
chcp 65001 > nul
title Water Meter USB Simulator GUI V1.2
python meter_simulator_gui.py
if errorlevel 1 pause

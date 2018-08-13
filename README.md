# CRC-Halflife
Python-application that connects to Capintec CRC-15 PET to periodically poll for activity readout. Calculates the halflife of radioactive isotopes, used in QC for radiotracers.

Connects throuth the RS-232 interface on the CRC-15.

Installation
* Install Python with recquired modules (WX, scipy, numpy and matplotlib). A pythonXY setup should run it out-of-the-box, and is the recommended environment to get started quickly.
* Ensure a serial cable / usr-to-rs232 adapter is connected, and set to COM1 (or change com port in -.py file)
* Run crc-15pet.py

Features:
* Displays a plot of the acqusitions, with real-time curve fitting performed for each new data point
* Displays the active radioisotope profile selected on the CRC-15 in background of the plot
* Auto-selects isotope based on CRC-15 data
* Can set data acquisition interval
* Can set number of halflifes (t1/2) to capture for, before automatically generating a report (PDF).
* Can send report automatically to default printer
* Generates report with field for signature
* Ask for username and batch number upon start, which are added to report
* Saves raw data compressed with gzip as a backup
* Automatically saves report PDF
* If no CRC15 is connected/found, it will emulate an output

Known issues
* Does not auto detect serial port, needs to be set in crc-15pet.py
* Only runs on windows due to printer functionality. Can easily be changed to run on other platforms as well.

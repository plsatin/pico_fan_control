# SPDX-FileCopyrightText: 2020 ladyada for Adafruit Industries
# SPDX-License-Identifier: MIT

# Simple demo of printing the temperature from the first found DS18x20 sensor every second.
# Author: Tony DiCola

# A 4.7Kohm pullup between DATA and POWER is REQUIRED!

import time

print('Start ...')

import board
from adafruit_onewire.bus import OneWireBus

from adafruit_ds18x20 import DS18X20

from digitalio import DigitalInOut, Direction, Pull


# Initialize one-wire bus on board pin GP22.
ow_bus = OneWireBus(board.GP22)

# Реле на GP15
relay_1 = DigitalInOut(board.GP15)
relay_1.direction = Direction.OUTPUT

# Scan for sensors and grab the first one found.
ds18 = DS18X20(ow_bus, ow_bus.scan()[0])

# Main loop to print the temperature every second.
while True:
    if ds18.temperature > 30.0:
        # print(f"Температура ({ds18.temperature:0.3f}C) выше нормы!")
        print(ds18.temperature)
        relay_1.value = True

    else:
        # print(f"Температура: {ds18.temperature:0.3f}C")
        print(ds18.temperature)
        relay_1.value = False

    time.sleep(5.0)
